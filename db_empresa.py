"""
Sincronização com o banco MariaDB da empresa.
OS:     1x por dia (agendado no app.py)
Frotas: a cada hora no minuto :31 (agendado no app.py)
Filtro fixo: id_empresa = 8
"""

import sqlite3
import os
from datetime import datetime, date

import mysql.connector

# ── Credenciais (lidas de variáveis de ambiente) ───────────────────────────────
_MYSQL_CFG = dict(
    host               = os.environ.get('DB_HOST', ''),
    user               = os.environ.get('DB_USER', ''),
    password           = os.environ.get('DB_PASSWORD', ''),
    port               = int(os.environ.get('DB_PORT', 3306)),
    database           = os.environ.get('DB_NAME', 'syscustoWeb'),
    connection_timeout = 15,
)
ID_EMPRESA = 8

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_SQLITE  = os.path.join(BASE_DIR, 'controle_abastecimento.db')

_MANUT_MAP = {
    'corretiva':  'C',
    'preventiva': 'P',
    'reforma':    'R',
    'básica':     'B',
    'basica':     'B',
    'preditiva':  'D',
}

def _manut(txt):
    return _MANUT_MAP.get((txt or '').strip().lower(), 'C')


def _sqlite():
    conn = sqlite3.connect(_SQLITE)
    conn.row_factory = sqlite3.Row
    return conn


def _mysql():
    return mysql.connector.connect(**_MYSQL_CFG)


# ── Sync Frotas ────────────────────────────────────────────────────────────────
def sync_frotas():
    """
    Fonte: vw_bi_fluxo_dFrtoa WHERE id_empresa = 8 AND proprio = 'sim'
    CodFrota → cod_frota  (chave que bate com codigo_frota/veiculo nas OS)
    """
    my  = _mysql()
    cur = my.cursor(dictionary=True)
    cur.execute("""
        SELECT
            CodFrota                    AS cod_frota,
            descricao_frota             AS descricao,
            descricao_especialidade     AS especialidade,
            descricao_especialidadeAgrup AS agrupamento,
            proprio
        FROM vw_bi_fluxo_dFrota
        WHERE id_empresa = %s
          AND LOWER(proprio) = 'sim'
    """, (ID_EMPRESA,))
    rows = cur.fetchall()
    cur.close()
    my.close()

    vistos   = set()
    registros = []
    for r in rows:
        cod = str(r['cod_frota'] or '').strip()
        if not cod or cod in vistos:
            continue
        vistos.add(cod)
        registros.append((
            cod,                                       # id_frota  (usa cod como PK)
            str(r['descricao']    or '').strip(),
            str(r['especialidade'] or '').strip(),
            str(r['agrupamento']  or '').strip(),
            'SIM',                                     # proprio (filtrado acima)
            cod,                                       # cod_frota
        ))

    db = _sqlite()
    db.execute('DELETE FROM frotas_disp')
    db.executemany(
        'INSERT INTO frotas_disp '
        '(id_frota, descricao, especialidade, agrupamento, proprio, cod_frota) '
        'VALUES (?,?,?,?,?,?)',
        registros
    )
    db.execute(
        "INSERT OR REPLACE INTO sync_log (chave, ultima_sync, total) VALUES ('frotas', ?, ?)",
        (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), len(registros))
    )
    db.commit()
    db.close()
    print(f"[sync_frotas] {len(registros)} frotas sincronizadas — {datetime.now():%H:%M:%S}")


# ── Sync OS ───────────────────────────────────────────────────────────────────
def sync_os():
    """
    Fonte: vw_ordem_servico_frota WHERE id_empresa = 8
           AND descricao_status IN ('Aberta', 'Execucao', 'Execução')
    """
    my  = _mysql()
    cur = my.cursor(dictionary=True)
    cur.execute("""
        SELECT
            documento,
            codigo_status,
            descricao_status,
            data_hora_abertura,
            data_hora_liberacao,
            codigo_frota,
            descricao_frota,
            descricao_tipo_manutencao,
            descricao_centro_custo,
            descricao_oficina,
            descricao_tipo_os,
            descricao_solicitante,
            descricao_problema,
            descricao_especialidadeAgrup   AS agrupamento,
            descricao_especialidade_frota  AS especialidade_micro
        FROM vw_ordem_servico_frota
        WHERE id_empresa = %s
          AND descricao_status IN ('Aberta', 'Execucao', 'Execução')
    """, (ID_EMPRESA,))
    rows = cur.fetchall()
    cur.close()
    my.close()

    hoje      = date.today()
    registros = []
    for r in rows:
        abertura  = r['data_hora_abertura']
        liberacao = r['data_hora_liberacao']

        data_ab_str  = abertura.strftime('%Y-%m-%d')  if abertura  else ''
        data_lib_str = liberacao.strftime('%Y-%m-%d') if liberacao else ''

        dias = 0
        if abertura:
            dt_ab = abertura.date() if hasattr(abertura, 'date') else abertura
            if liberacao:
                dt_lb = liberacao.date() if hasattr(liberacao, 'date') else liberacao
                dias  = max(0, (dt_lb - dt_ab).days)
            else:
                dias  = max(0, (hoje - dt_ab).days)

        # Mapeia descricao_status para codigo_status legado usado no dashboard
        desc_st = str(r.get('descricao_status') or '').strip().lower()
        if 'aberta' in desc_st:
            status = 'A'
        elif 'execu' in desc_st:
            status = 'E'
        else:
            status = str(r['codigo_status'] or '').strip()

        registros.append((
            str(r['documento']              or ''),
            status,
            data_ab_str,
            str(r['codigo_frota']           or ''),   # veiculo — bate com cod_frota das frotas
            '',                                        # placa (não disponível na view)
            str(r['descricao_frota']        or ''),
            _manut(r['descricao_tipo_manutencao']),
            str(r['descricao_centro_custo'] or ''),
            str(r['descricao_oficina']      or ''),
            str(r['descricao_tipo_os']      or ''),
            str(r['descricao_solicitante']  or ''),
            data_lib_str,
            '',                                        # fundo_agricola
            0.0,                                       # custo_oficina
            0.0,                                       # custo_mao_obra
            0.0,                                       # pecas_consumidas
            0.0,                                       # valor_os
            str(r['descricao_problema']     or ''),
            str(r['agrupamento']            or ''),
            dias,
        ))

    db = _sqlite()
    db.execute('DELETE FROM os_registros')
    db.executemany('''
        INSERT INTO os_registros (
            nro_os, status, data_abertura, veiculo, placa, descricao_veiculo,
            manutencao, centro_custo_nome, oficina_nome, tipo_os,
            solicitante_nome, data_liberacao, fundo_agricola,
            custo_oficina, custo_mao_obra, pecas_consumidas, valor_os,
            descricao_problema, especialidade, dias_aberta
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    ''', registros)
    db.execute(
        "INSERT OR REPLACE INTO sync_log (chave, ultima_sync, total) VALUES ('os', ?, ?)",
        (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), len(registros))
    )
    db.commit()
    db.close()
    print(f"[sync_os] {len(registros)} OS sincronizadas — {datetime.now():%H:%M:%S}")


if __name__ == '__main__':
    print("Testando sync_frotas...")
    sync_frotas()
    print("Testando sync_os...")
    sync_os()
    print("Concluído.")
