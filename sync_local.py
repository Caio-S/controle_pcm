"""
Script local de sincronização — roda no PC da empresa.
Puxa dados do MySQL e envia via HTTPS para o PythonAnywhere.

Agendar no Agendador de Tarefas do Windows:
  - Frotas: 1x por dia às 03:00
  - OS:     todo minuto :31 (ou a cada hora, conforme necessidade)

Uso:
  python sync_local.py os
  python sync_local.py frotas
  python sync_local.py ambos
"""

import sys
import json
import urllib.request
import urllib.error
from datetime import datetime, date

import mysql.connector

# ── Configurações ──────────────────────────────────────────────────────────────
PYTHONANYWHERE_URL = 'https://pcmoficina.pythonanywhere.com/api/push_sync'
SYNC_TOKEN         = 'pcm_sync_japungu_2025'   # mesmo valor que app.py

MYSQL_CFG = dict(
    host               = 'rdscontroladoria-read.grupojapungu.com',
    user               = 'caio_souza',
    password           = 'MECAGaYiHIyIq4HE2i43vOzIped3Ke',
    port               = 3306,
    database           = 'syscustoWeb',
    connection_timeout = 15,
)
ID_EMPRESA = 8

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

# ── Coleta frotas ──────────────────────────────────────────────────────────────
def coletar_frotas():
    conn = mysql.connector.connect(**MYSQL_CFG)
    cur  = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT
            CodFrota                     AS cod_frota,
            descricao_frota              AS descricao,
            descricao_especialidade      AS especialidade,
            descricao_especialidadeAgrup AS agrupamento
        FROM vw_bi_fluxo_dFrota
        WHERE id_empresa = %s
          AND LOWER(proprio) = 'sim'
    """, (ID_EMPRESA,))
    rows = cur.fetchall()
    cur.close(); conn.close()

    vistos, resultado = set(), []
    for r in rows:
        cod = str(r['cod_frota'] or '').strip()
        if not cod or cod in vistos:
            continue
        vistos.add(cod)
        resultado.append({
            'cod_frota':    cod,
            'descricao':    str(r['descricao']    or '').strip(),
            'especialidade':str(r['especialidade'] or '').strip(),
            'agrupamento':  str(r['agrupamento']  or '').strip(),
        })
    print(f'[frotas] {len(resultado)} frotas coletadas')
    return resultado


# ── Coleta OS ──────────────────────────────────────────────────────────────────
def coletar_os():
    conn = mysql.connector.connect(**MYSQL_CFG)
    cur  = conn.cursor(dictionary=True)
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
            descricao_especialidadeAgrup  AS agrupamento
        FROM vw_ordem_servico_frota
        WHERE id_empresa = %s
          AND descricao_status IN ('Aberta', 'Execucao', 'Execução')
    """, (ID_EMPRESA,))
    rows = cur.fetchall()
    cur.close(); conn.close()

    hoje      = date.today()
    resultado = []
    for r in rows:
        abertura  = r['data_hora_abertura']
        liberacao = r['data_hora_liberacao']
        data_ab   = abertura.strftime('%Y-%m-%d')  if abertura  else ''
        data_lib  = liberacao.strftime('%Y-%m-%d') if liberacao else ''

        dias = 0
        if abertura:
            dt_ab = abertura.date() if hasattr(abertura, 'date') else abertura
            if liberacao:
                dt_lb = liberacao.date() if hasattr(liberacao, 'date') else liberacao
                dias  = max(0, (dt_lb - dt_ab).days)
            else:
                dias  = max(0, (hoje - dt_ab).days)

        desc_st = str(r.get('descricao_status') or '').strip().lower()
        if   'aberta' in desc_st: status = 'A'
        elif 'execu'  in desc_st: status = 'E'
        else: status = str(r['codigo_status'] or '').strip()

        resultado.append({
            'nro_os':           str(r['documento']              or ''),
            'status':           status,
            'data_abertura':    data_ab,
            'veiculo':          str(r['codigo_frota']           or ''),
            'descricao_veiculo':str(r['descricao_frota']        or ''),
            'manutencao':       _manut(r['descricao_tipo_manutencao']),
            'centro_custo_nome':str(r['descricao_centro_custo'] or ''),
            'oficina_nome':     str(r['descricao_oficina']      or ''),
            'tipo_os':          str(r['descricao_tipo_os']      or ''),
            'solicitante_nome': str(r['descricao_solicitante']  or ''),
            'data_liberacao':   data_lib,
            'descricao_problema':str(r['descricao_problema']    or ''),
            'agrupamento':      str(r['agrupamento']            or ''),
            'dias_aberta':      dias,
        })

    print(f'[os] {len(resultado)} OS coletadas')
    return resultado


# ── Envio para PythonAnywhere ──────────────────────────────────────────────────
def enviar(payload: dict):
    body    = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    req     = urllib.request.Request(
        PYTHONANYWHERE_URL,
        data    = body,
        headers = {
            'Content-Type': 'application/json',
            'X-Sync-Token': SYNC_TOKEN,
        },
        method  = 'POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resultado = json.loads(resp.read())
            print(f'[envio] Resposta: {resultado}')
            return resultado.get('ok', False)
    except urllib.error.HTTPError as e:
        print(f'[envio] Erro HTTP {e.code}: {e.read().decode()}')
        return False
    except Exception as e:
        print(f'[envio] Erro: {e}')
        return False


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    modo = (sys.argv[1] if len(sys.argv) > 1 else 'ambos').lower()
    print(f'[sync_local] {datetime.now():%Y-%m-%d %H:%M:%S} — modo: {modo}')

    payload = {}

    if modo in ('frotas', 'ambos'):
        try:
            payload['frotas'] = coletar_frotas()
        except Exception as e:
            print(f'[frotas] ERRO: {e}')

    if modo in ('os', 'ambos'):
        try:
            payload['os'] = coletar_os()
        except Exception as e:
            print(f'[os] ERRO: {e}')

    if payload:
        ok = enviar(payload)
        print('[sync_local] Concluído com', 'sucesso' if ok else 'FALHA')
    else:
        print('[sync_local] Nada para enviar.')


if __name__ == '__main__':
    main()
