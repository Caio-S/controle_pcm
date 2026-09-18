from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_file
from werkzeug.utils import secure_filename
import sqlite3
from datetime import datetime, timedelta
import os
import csv
import io
import re
from collections import Counter
from werkzeug.security import generate_password_hash, check_password_hash
from PIL import Image, ImageOps

app = Flask(__name__)
app.secret_key = 'senha_secreta_pcm'

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
caminho_bd = os.path.join(BASE_DIR, 'controle_abastecimento.db')

def get_db_connection():
    conn = sqlite3.connect(caminho_bd)
    conn.row_factory = sqlite3.Row
    return conn

def normalizar_id_frota(valor):
    frota = str(valor or '').strip().upper()
    partes = [p.strip() for p in re.split(r'\s*-\s*', frota) if p.strip()]
    if len(partes) > 1 and all(re.fullmatch(r'\d+', p) for p in partes):
        return ' - '.join(partes)
    return frota

def importar_catalogo_ferramentas_csv(conn, csv_path):
    if not os.path.exists(csv_path):
        return 0
    total_atual = conn.execute('SELECT COUNT(*) FROM ferramentaria_ferramentas').fetchone()[0]
    if total_atual > 0:
        return 0

    with open(csv_path, 'rb') as f:
        content_bytes = f.read()
    try:
        content_str = content_bytes.decode('utf-8-sig')
    except:
        content_str = content_bytes.decode('latin1', errors='ignore')

    reader = csv.DictReader(io.StringIO(content_str), delimiter=';')
    lote = []
    importadas = 0
    for row in reader:
        codigo = str(row.get('codprod') or row.get('CODPROD') or '').strip()
        descricao = str(row.get('descprod') or row.get('DESCPROD') or '').strip().upper()
        if not codigo or not descricao:
            continue
        lote.append((codigo, descricao))
        if len(lote) >= 5000:
            conn.executemany('''
                INSERT OR IGNORE INTO ferramentaria_ferramentas (codigo, descricao)
                VALUES (?, ?)
            ''', lote)
            importadas += len(lote)
            lote = []
    if lote:
        conn.executemany('''
            INSERT OR IGNORE INTO ferramentaria_ferramentas (codigo, descricao)
            VALUES (?, ?)
        ''', lote)
        importadas += len(lote)
    return importadas

def setup_db():
    conn = sqlite3.connect(caminho_bd)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS frotas (id_frota TEXT PRIMARY KEY, descricao TEXT, setor TEXT, status_abastecimento TEXT DEFAULT 'LIBERADO')''')
    try: cursor.execute("ALTER TABLE frotas ADD COLUMN especialidade TEXT DEFAULT 'OUTROS'")
    except: pass
    try: cursor.execute("ALTER TABLE frotas ADD COLUMN agrupamento TEXT DEFAULT 'GERAL'")
    except: pass
    # Turno de operação (A/B/C). Fica vazio até ser cadastrado; o relatório de
    # programação usa isto para separar as faixas de turno, como na planilha.
    try: cursor.execute("ALTER TABLE frotas ADD COLUMN turno TEXT DEFAULT ''")
    except: pass
    # Conjunto: frotas que rodam juntas (cavalo + reboques) e são programadas
    # juntas. Duas frotas com o mesmo `conjunto` aparecem coladas na mesma célula.
    try: cursor.execute("ALTER TABLE frotas ADD COLUMN conjunto TEXT DEFAULT ''")
    except: pass

    cursor.execute('''CREATE TABLE IF NOT EXISTS frotas_disp (
        id_frota TEXT PRIMARY KEY,
        descricao TEXT DEFAULT '',
        especialidade TEXT DEFAULT '',
        agrupamento TEXT DEFAULT '',
        proprio TEXT DEFAULT 'SIM'
    )''')
    try: cursor.execute("ALTER TABLE frotas_disp ADD COLUMN cod_frota TEXT DEFAULT ''")
    except: pass

    cursor.execute('''CREATE TABLE IF NOT EXISTS historico_bloqueios (id INTEGER PRIMARY KEY AUTOINCREMENT, id_frota TEXT, motivo TEXT, status_oficina TEXT, data_bloqueio TEXT, data_desbloqueio TEXT, observacao TEXT, FOREIGN KEY(id_frota) REFERENCES frotas(id_frota))''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS programacao_semanal (id INTEGER PRIMARY KEY AUTOINCREMENT, id_frota TEXT, tipo_servico TEXT, data_planejada TEXT, status TEXT DEFAULT 'PENDENTE', data_execucao TEXT, FOREIGN KEY(id_frota) REFERENCES frotas(id_frota))''')

    try: cursor.execute("ALTER TABLE programacao_semanal ADD COLUMN local_execucao TEXT DEFAULT 'BASE'")
    except: pass
    try: cursor.execute("ALTER TABLE programacao_semanal ADD COLUMN resultado_analise TEXT DEFAULT ''")
    except: pass
    try: cursor.execute("ALTER TABLE programacao_semanal ADD COLUMN observacao TEXT DEFAULT ''")
    except: pass
    try: cursor.execute("ALTER TABLE programacao_semanal ADD COLUMN origem_classificacao TEXT DEFAULT 'MANUAL'")
    except: pass
    try: cursor.execute("ALTER TABLE programacao_semanal ADD COLUMN os_vinculada TEXT DEFAULT ''")
    except: pass
    try: cursor.execute("ALTER TABLE programacao_semanal ADD COLUMN motivo_classificacao TEXT DEFAULT ''")
    except: pass

    cursor.execute('''CREATE TABLE IF NOT EXISTS servicos_externos (
        nro_os TEXT PRIMARY KEY,
        id_frota TEXT,
        fornecedor TEXT,
        data_abertura TEXT,
        status_os TEXT,
        orcamento INTEGER DEFAULT 0,
        solicitacao INTEGER DEFAULT 0,
        cotacao INTEGER DEFAULT 0,
        pedido INTEGER DEFAULT 0,
        previsao_atual TEXT,
        FOREIGN KEY(id_frota) REFERENCES frotas(id_frota)
    )''')

    try: cursor.execute("ALTER TABLE servicos_externos ADD COLUMN data_fechamento TEXT")
    except: pass
    try: cursor.execute("ALTER TABLE servicos_externos ADD COLUMN observacao TEXT DEFAULT ''")
    except: pass
    try: cursor.execute("ALTER TABLE servicos_externos ADD COLUMN orcamento_numero TEXT DEFAULT ''")
    except: pass
    try: cursor.execute("ALTER TABLE servicos_externos ADD COLUMN solicitacao_numero TEXT DEFAULT ''")
    except: pass
    try: cursor.execute("ALTER TABLE servicos_externos ADD COLUMN cotacao_numero TEXT DEFAULT ''")
    except: pass
    try: cursor.execute("ALTER TABLE servicos_externos ADD COLUMN pedido_numero TEXT DEFAULT ''")
    except: pass

    cursor.execute('''CREATE TABLE IF NOT EXISTS chb_turno_registros (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        data_execucao TEXT,
        hora_inicial TEXT,
        hora_final TEXT,
        turno TEXT,
        os_integrada TEXT,
        veiculo TEXT,
        descricao_veiculo TEXT,
        oficina TEXT,
        descricao_oficina TEXT,
        mecanico TEXT,
        nome_mecanico TEXT,
        servico TEXT,
        descricao_servico TEXT,
        compartimento TEXT,
        descricao_compartimento TEXT,
        quantidade REAL
    )''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS historico_previsoes_externas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nro_os TEXT,
        data_registro TEXT,
        previsao TEXT
    )''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS abastecimentos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        id_frota TEXT,
        data_abastecimento TEXT,
        quantidade TEXT,
        km_abast TEXT,
        media TEXT,
        UNIQUE(id_frota, data_abastecimento, quantidade, km_abast)
    )''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS responsaveis (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT,
        agrupamento TEXT,
        especialidade TEXT,
        UNIQUE(agrupamento, especialidade)
    )''')

    # Grupos de responsabilidade: cada grupo tem um nome (ex: "Frente 1", "Caminhões"),
    # um único responsável e uma lista de frotas específicas. Substitui o modelo antigo
    # de macro-grupo geral (tabela 'responsaveis' acima, mantida só por histórico).
    cursor.execute('''CREATE TABLE IF NOT EXISTS grupos_responsaveis (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome_grupo TEXT NOT NULL UNIQUE,
        responsavel_nome TEXT NOT NULL,
        criado_em TEXT
    )''')
    # UNIQUE em id_frota garante que cada frota pertença a um único grupo por vez.
    cursor.execute('''CREATE TABLE IF NOT EXISTS grupo_frotas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        grupo_id INTEGER NOT NULL,
        id_frota TEXT NOT NULL UNIQUE
    )''')

    # Migração única: converte os vínculos antigos de macro-grupo (agrupamento/especialidade)
    # em grupos equivalentes já com a lista real de frotas, para não perder o que já estava
    # configurado. Só roda se ainda não existir nenhum grupo (não repete em reinícios futuros).
    if cursor.execute('SELECT COUNT(*) FROM grupos_responsaveis').fetchone()[0] == 0:
        macros_antigos = cursor.execute('SELECT DISTINCT nome, agrupamento, especialidade FROM responsaveis').fetchall()
        for nome_antigo, agrupamento_antigo, especialidade_antiga in macros_antigos:
            nome_grupo = agrupamento_antigo
            sufixo = 1
            while cursor.execute('SELECT id FROM grupos_responsaveis WHERE nome_grupo=?', (nome_grupo,)).fetchone():
                sufixo += 1
                nome_grupo = f"{agrupamento_antigo} ({sufixo})"
            ts_migracao = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            cursor.execute('INSERT INTO grupos_responsaveis (nome_grupo, responsavel_nome, criado_em) VALUES (?, ?, ?)',
                           (nome_grupo, nome_antigo, ts_migracao))
            novo_grupo_id = cursor.lastrowid
            if especialidade_antiga and especialidade_antiga != 'TODAS':
                frotas_migrar = cursor.execute('SELECT id_frota FROM frotas WHERE agrupamento=? AND especialidade=?', (agrupamento_antigo, especialidade_antiga)).fetchall()
            else:
                frotas_migrar = cursor.execute('SELECT id_frota FROM frotas WHERE agrupamento=?', (agrupamento_antigo,)).fetchall()
            for (id_frota_migrar,) in frotas_migrar:
                try:
                    cursor.execute('INSERT OR IGNORE INTO grupo_frotas (grupo_id, id_frota) VALUES (?, ?)', (novo_grupo_id, id_frota_migrar))
                except Exception:
                    pass

    cursor.execute('''CREATE TABLE IF NOT EXISTS analises_oleo (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        id_frota TEXT,
        data_coleta TEXT,
        compartimento TEXT,
        diagnostico TEXT,
        parecer TEXT,
        tratativa TEXT DEFAULT '',
        status_tratativa TEXT DEFAULT 'PENDENTE',
        data_tratativa TEXT,
        UNIQUE(id_frota, data_coleta, compartimento)
    )''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS perfis_acesso (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT UNIQUE,
        acesso_bloqueios INTEGER DEFAULT 1,
        acesso_programacao INTEGER DEFAULT 1,
        acesso_lavagem INTEGER DEFAULT 1,
        acesso_analises INTEGER DEFAULT 1,
        acesso_abastecimentos INTEGER DEFAULT 1,
        acesso_externos INTEGER DEFAULT 1,
        acesso_ferramentaria INTEGER DEFAULT 0,
        senha_hash TEXT DEFAULT ''
    )''')
    try: cursor.execute("ALTER TABLE perfis_acesso ADD COLUMN senha_hash TEXT DEFAULT ''")
    except: pass
    try: cursor.execute("ALTER TABLE perfis_acesso ADD COLUMN acesso_lavagem INTEGER DEFAULT 1")
    except: pass
    try: cursor.execute("ALTER TABLE perfis_acesso ADD COLUMN acesso_ferramentaria INTEGER DEFAULT 0")
    except: pass
    try: cursor.execute("ALTER TABLE compras_solicitacoes ADD COLUMN encarregado TEXT DEFAULT ''")
    except: pass
    try: cursor.execute("ALTER TABLE perfis_acesso ADD COLUMN acesso_compras INTEGER DEFAULT 0")
    except: pass
    try: cursor.execute("ALTER TABLE perfis_acesso ADD COLUMN compras_criar INTEGER DEFAULT 1")
    except: pass
    try: cursor.execute("ALTER TABLE perfis_acesso ADD COLUMN compras_editar INTEGER DEFAULT 1")
    except: pass
    try: cursor.execute("ALTER TABLE perfis_acesso ADD COLUMN compras_alterar_status INTEGER DEFAULT 1")
    except: pass
    try: cursor.execute("ALTER TABLE perfis_acesso ADD COLUMN senha_txt TEXT DEFAULT ''")
    except: pass

    csv_path = os.path.join(BASE_DIR, 'base_frotas.csv')
    if os.path.exists(csv_path):
        try:
            with open(csv_path, 'rb') as f:
                content_bytes = f.read()
                try: content_str = content_bytes.decode('utf-8-sig')
                except: content_str = content_bytes.decode('latin1', errors='ignore')
                reader = csv.DictReader(io.StringIO(content_str), delimiter=';')
                for raw_row in reader:
                    row = {k.strip().upper(): v for k, v in raw_row.items() if k}

                    frota = str(row.get('CODFROTA', row.get('ID_FROTA', row.get('FROTA', '')))).strip()
                    esp = str(row.get('DESCRICAO_ESPECIALIDADE', row.get('ESPECIALIDADE', ''))).strip() or 'OUTROS'
                    agrup = str(row.get('DESCRICAO_ESPECIALIDADEAGRUP', row.get('AGRUPAMENTO', ''))).strip() or 'GERAL'
                    desc = str(
                        row.get('DESCRICAO_FROTA') or
                        row.get('DESCRICAO') or
                        row.get('DESCRIÃ‡ÃƒO') or
                        row.get('MODELO') or
                        row.get('VEICULO') or
                        row.get('VEÍCULO') or
                        row.get('DESCRIÃ‡ÃƒO VEÍCULO') or
                        row.get('DESCRICAO VEICULO') or
                        row.get('NOME') or
                        row.get('EQUIPAMENTO') or
                        ''
                    ).strip()

                    if frota:
                        # Só atualiza especialidade/agrupamento se ainda estiverem com valor padrão (vazio, OUTROS, GERAL)
                        # Isso evita sobrescrever customizações feitas manualmente pelo "Gerir Setores"
                        if esp and esp.upper() not in ['OUTROS', '']:
                            cursor.execute("""
                                UPDATE frotas SET especialidade = ?
                                WHERE id_frota = ?
                                AND (especialidade IS NULL OR TRIM(especialidade) = '' OR UPPER(TRIM(especialidade)) = 'OUTROS')
                            """, (esp, frota))
                        if agrup and agrup.upper() not in ['GERAL', '']:
                            cursor.execute("""
                                UPDATE frotas SET agrupamento = ?
                                WHERE id_frota = ?
                                AND (agrupamento IS NULL OR TRIM(agrupamento) = '' OR UPPER(TRIM(agrupamento)) = 'GERAL')
                            """, (agrup, frota))
                        if desc and desc.lower() not in ['none', 'null', '']:
                            cursor.execute("UPDATE frotas SET descricao = ? WHERE id_frota = ? AND (descricao IS NULL OR TRIM(descricao) = '' OR descricao = 'SEM DESCRIÇÃO')", (desc, frota))
                        cursor.execute("INSERT OR IGNORE INTO frotas (id_frota, descricao, setor, especialidade, agrupamento) VALUES (?, ?, '', ?, ?)", (frota, desc if desc else 'SEM DESCRIÇÃO', esp, agrup))
        except Exception as e: print("Erro na Auto-Cura:", e)

    try:
        merged = cursor.execute("SELECT * FROM programacao_semanal WHERE tipo_servico LIKE '%+%'").fetchall()
        for row in merged:
            tipos = [t.strip() for t in row['tipo_servico'].split('+')]
            if len(tipos) > 1:
                cursor.execute("UPDATE programacao_semanal SET tipo_servico = ? WHERE id = ?", (tipos[0], row['id']))
                for t in tipos[1:]:
                    if t: cursor.execute("INSERT INTO programacao_semanal (id_frota, tipo_servico, data_planejada, status, data_execucao, local_execucao) VALUES (?, ?, ?, ?, ?, ?)", (row['id_frota'], t, row['data_planejada'], row['status'], row['data_execucao'], row.get('local_execucao', 'BASE')))
    except Exception as e: pass

    try:
        conn.execute('DELETE FROM programacao_semanal WHERE id NOT IN (SELECT MAX(id) FROM programacao_semanal GROUP BY id_frota, tipo_servico, data_planejada, observacao)')
    except Exception as e: pass

    cursor.execute('''CREATE TABLE IF NOT EXISTS reforma_maquinas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        id_frota TEXT NOT NULL,
        ano_fabricacao TEXT DEFAULT '',
        km_horimetro TEXT DEFAULT '',
        dt_ultima_reforma TEXT DEFAULT '',
        observacao TEXT DEFAULT '',
        UNIQUE(id_frota),
        FOREIGN KEY(id_frota) REFERENCES frotas(id_frota)
    )''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS reforma_programacao (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        id_frota TEXT NOT NULL,
        ano INTEGER NOT NULL,
        mes INTEGER NOT NULL,
        status TEXT DEFAULT 'PENDENTE',
        observacao TEXT DEFAULT '',
        UNIQUE(id_frota, ano, mes),
        FOREIGN KEY(id_frota) REFERENCES frotas(id_frota)
    )''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS reforma_importacoes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        arquivo TEXT NOT NULL,
        data_importacao TEXT NOT NULL,
        total_abas INTEGER DEFAULT 0,
        total_linhas INTEGER DEFAULT 0,
        observacao TEXT DEFAULT ''
    )''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS reforma_planilha_linhas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        importacao_id INTEGER NOT NULL,
        aba TEXT NOT NULL,
        linha INTEGER NOT NULL,
        dados_json TEXT NOT NULL,
        UNIQUE(importacao_id, aba, linha),
        FOREIGN KEY(importacao_id) REFERENCES reforma_importacoes(id)
    )''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS reforma_planejamento_planilha (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        importacao_id INTEGER NOT NULL,
        aba TEXT NOT NULL,
        id_frota TEXT NOT NULL,
        agrupamento TEXT DEFAULT '',
        especialidade TEXT DEFAULT '',
        ano_fabricacao TEXT DEFAULT '',
        km_horimetro TEXT DEFAULT '',
        dt_ultima_reforma TEXT DEFAULT '',
        data_programada TEXT NOT NULL,
        ano INTEGER NOT NULL,
        mes INTEGER NOT NULL,
        status TEXT NOT NULL,
        observacao TEXT DEFAULT '',
        UNIQUE(importacao_id, aba, id_frota, ano, mes, status),
        FOREIGN KEY(importacao_id) REFERENCES reforma_importacoes(id)
    )''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS reforma_os_planilha (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        importacao_id INTEGER NOT NULL,
        aba TEXT NOT NULL,
        documento TEXT,
        id_frota TEXT,
        empresa TEXT,
        status_os TEXT,
        data_abertura TEXT,
        data_liberacao TEXT,
        tipo_manutencao TEXT,
        centro_custo TEXT,
        especialidade TEXT,
        agrupamento TEXT,
        descricao_frota TEXT,
        dados_json TEXT NOT NULL,
        UNIQUE(importacao_id, aba, documento, id_frota),
        FOREIGN KEY(importacao_id) REFERENCES reforma_importacoes(id)
    )''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS reforma_gastos_planilha (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        importacao_id INTEGER NOT NULL,
        aba TEXT NOT NULL,
        id_frota TEXT NOT NULL,
        descricao_frota TEXT DEFAULT '',
        empresa TEXT DEFAULT '',
        ano INTEGER NOT NULL,
        mes INTEGER NOT NULL,
        especialidade TEXT DEFAULT '',
        valor REAL NOT NULL,
        UNIQUE(importacao_id, aba, id_frota, ano, mes, valor),
        FOREIGN KEY(importacao_id) REFERENCES reforma_importacoes(id)
    )''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS reforma_orcamento_planilha (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        importacao_id INTEGER NOT NULL,
        aba TEXT NOT NULL,
        id_frota TEXT NOT NULL,
        modelo TEXT DEFAULT '',
        descricao_frota TEXT DEFAULT '',
        ano_fabricacao TEXT DEFAULT '',
        especialidade TEXT DEFAULT '',
        agrupamento TEXT DEFAULT '',
        componente TEXT NOT NULL,
        valor REAL NOT NULL,
        UNIQUE(importacao_id, aba, id_frota, componente),
        FOREIGN KEY(importacao_id) REFERENCES reforma_importacoes(id)
    )''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS ferramentaria_retiradas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mecanico TEXT NOT NULL,
        frota TEXT DEFAULT '',
        local TEXT DEFAULT '',
        data_retirada TEXT NOT NULL,
        data_devolucao TEXT,
        observacao TEXT DEFAULT '',
        criado_por TEXT DEFAULT '',
        criado_em TEXT NOT NULL
    )''')
    try: cursor.execute("ALTER TABLE ferramentaria_retiradas ADD COLUMN data_devolucao TEXT")
    except: pass

    cursor.execute('''CREATE TABLE IF NOT EXISTS ferramentaria_retirada_itens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        retirada_id INTEGER NOT NULL,
        codigo TEXT DEFAULT '',
        descricao TEXT NOT NULL,
        quantidade REAL DEFAULT 1,
        valor_unitario REAL DEFAULT 0,
        FOREIGN KEY(retirada_id) REFERENCES ferramentaria_retiradas(id)
    )''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS ferramentaria_ferramentas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        codigo TEXT NOT NULL UNIQUE,
        descricao TEXT NOT NULL,
        estoque_total REAL DEFAULT 0,
        estoque_minimo REAL DEFAULT 0,
        ativo INTEGER DEFAULT 1,
        atualizado_em TEXT
    )''')
    try: cursor.execute("ALTER TABLE ferramentaria_ferramentas ADD COLUMN estoque_total REAL DEFAULT 0")
    except: pass
    try: cursor.execute("ALTER TABLE ferramentaria_ferramentas ADD COLUMN estoque_minimo REAL DEFAULT 0")
    except: pass
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_ferramentaria_ferramentas_descricao ON ferramentaria_ferramentas(descricao)')

    cursor.execute('''CREATE TABLE IF NOT EXISTS frotas_modelos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        id_frota TEXT NOT NULL UNIQUE,
        modelo TEXT NOT NULL,
        especialidade TEXT DEFAULT ''
    )''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_frotas_modelos_modelo ON frotas_modelos(modelo)')

    cursor.execute('''CREATE TABLE IF NOT EXISTS filtros_catalogo (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        modelo TEXT NOT NULL,
        codigo_filtro TEXT NOT NULL,
        tipo_filtro TEXT NOT NULL,
        UNIQUE(modelo, codigo_filtro)
    )''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_filtros_catalogo_modelo ON filtros_catalogo(modelo)')

    filtros_csv = os.path.join(BASE_DIR, 'base_filtros.csv')
    if os.path.exists(filtros_csv):
        try:
            total_fm = cursor.execute('SELECT COUNT(*) FROM frotas_modelos').fetchone()[0]
            total_fc = cursor.execute('SELECT COUNT(*) FROM filtros_catalogo').fetchone()[0]
            if total_fm == 0 or total_fc == 0:
                with open(filtros_csv, 'rb') as f:
                    content_str = f.read().decode('utf-8-sig')
                reader = csv.DictReader(io.StringIO(content_str), delimiter=';')
                lote_fm, lote_fc = [], []
                for row in reader:
                    idf = str(row.get('id_frota', '')).strip()
                    mod = str(row.get('modelo', '')).strip()
                    esp = str(row.get('especialidade', '')).strip()
                    cod = str(row.get('codigo_filtro', '')).strip()
                    tip = str(row.get('tipo_filtro', '')).strip()
                    if idf and mod:
                        lote_fm.append((idf, mod, esp))
                    if mod and cod and tip:
                        lote_fc.append((mod, cod, tip))
                cursor.executemany('INSERT OR IGNORE INTO frotas_modelos (id_frota, modelo, especialidade) VALUES (?,?,?)', lote_fm)
                cursor.executemany('INSERT OR IGNORE INTO filtros_catalogo (modelo, codigo_filtro, tipo_filtro) VALUES (?,?,?)', lote_fc)
        except Exception as e:
            print("Erro ao importar base_filtros.csv:", e)

    cursor.execute('''CREATE TABLE IF NOT EXISTS associados (
        matricula TEXT PRIMARY KEY,
        nome TEXT NOT NULL
    )''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_associados_nome ON associados(nome)')
    for csv_associados in [
        os.path.join(BASE_DIR, 'base_associados.csv'),
        os.path.join(os.path.expanduser('~'), 'Downloads', 'FUNCIONARIO ATIVOS.csv')
    ]:
        if os.path.exists(csv_associados):
            try:
                with open(csv_associados, 'rb') as f:
                    content_bytes = f.read()
                try:
                    content_str = content_bytes.decode('utf-8-sig')
                except:
                    content_str = content_bytes.decode('latin1', errors='ignore')
                reader = csv.DictReader(io.StringIO(content_str), delimiter=';')
                lote = []
                for row in reader:
                    mat = str(row.get('matricula') or row.get('MATRICULA') or '').strip()
                    nom = str(row.get('nome') or row.get('NOME') or '').strip().upper()
                    if mat and nom:
                        lote.append((mat, nom))
                if lote:
                    cursor.execute('DELETE FROM associados')
                    cursor.executemany('INSERT OR REPLACE INTO associados (matricula, nome) VALUES (?, ?)', lote)
                break
            except Exception as e:
                print("Erro ao importar associados:", e)

    for ferramentas_csv in [
        os.path.join(BASE_DIR, 'base_ferramenta.csv'),
        os.path.join(os.path.expanduser('~'), 'Downloads', 'base_ferramenta.csv')
    ]:
        try:
            if importar_catalogo_ferramentas_csv(conn, ferramentas_csv):
                break
        except Exception as e:
            print("Erro ao importar base de ferramentas:", e)

    cursor.execute('''CREATE TABLE IF NOT EXISTS ferramentaria_agregados (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item INTEGER,
        categoria TEXT NOT NULL DEFAULT '',
        codigo_novo TEXT,
        codigo_recond TEXT,
        descricao TEXT NOT NULL,
        referencia TEXT,
        versao TEXT,
        saldo_novo INTEGER DEFAULT 0,
        p_conserto INTEGER DEFAULT 0,
        saldo_recond INTEGER DEFAULT 0,
        em_manut INTEGER DEFAULT 0,
        devendo INTEGER DEFAULT 0,
        modelo_maquina TEXT DEFAULT 'CH570',
        ativo INTEGER DEFAULT 1,
        atualizado_em TEXT
    )''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_agregados_modelo ON ferramentaria_agregados(modelo_maquina)')
    try: cursor.execute("ALTER TABLE ferramentaria_agregados ADD COLUMN imagem TEXT")
    except Exception: pass

    cursor.execute('''CREATE TABLE IF NOT EXISTS agregados_registros (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        agregado_id INTEGER NOT NULL,
        numero_fogo TEXT,
        frota TEXT,
        vida_equipamento TEXT,
        status TEXT DEFAULT 'EM ESTOQUE',
        observacoes TEXT,
        data_registro TEXT,
        usuario TEXT
    )''')
    # Migração: garante que a coluna status existe em instalações antigas
    try:
        cursor.execute("ALTER TABLE agregados_registros ADD COLUMN status TEXT DEFAULT 'EM ESTOQUE'")
    except Exception:
        pass
    try: cursor.execute("ALTER TABLE agregados_registros ADD COLUMN imagem TEXT")
    except Exception: pass

    os.makedirs(os.path.join(BASE_DIR, 'uploads', 'termos'), exist_ok=True)

    cursor.execute('''CREATE TABLE IF NOT EXISTS caixa_ferramentas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mecanico TEXT NOT NULL,
        observacao TEXT DEFAULT '',
        termo_pdf TEXT DEFAULT '',
        criado_por TEXT DEFAULT '',
        criado_em TEXT NOT NULL,
        atualizado_em TEXT
    )''')
    try: cursor.execute("ALTER TABLE caixa_ferramentas ADD COLUMN termo_pdf TEXT DEFAULT ''")
    except: pass
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_caixa_ferramentas_mecanico ON caixa_ferramentas(mecanico)')

    cursor.execute('''CREATE TABLE IF NOT EXISTS caixa_ferramentas_itens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        caixa_id INTEGER NOT NULL,
        codigo TEXT DEFAULT '',
        descricao TEXT NOT NULL,
        quantidade REAL DEFAULT 1,
        valor_unitario REAL DEFAULT 0,
        FOREIGN KEY(caixa_id) REFERENCES caixa_ferramentas(id)
    )''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS caminhao_oficina (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        identificacao TEXT NOT NULL,
        responsavel TEXT DEFAULT '',
        observacao TEXT DEFAULT '',
        termo_pdf TEXT DEFAULT '',
        criado_por TEXT DEFAULT '',
        criado_em TEXT NOT NULL,
        atualizado_em TEXT
    )''')
    try: cursor.execute("ALTER TABLE caminhao_oficina ADD COLUMN termo_pdf TEXT DEFAULT ''")
    except: pass
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_caminhao_oficina_ident ON caminhao_oficina(identificacao)')

    cursor.execute('''CREATE TABLE IF NOT EXISTS caminhao_oficina_itens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        caminhao_id INTEGER NOT NULL,
        codigo TEXT DEFAULT '',
        descricao TEXT NOT NULL,
        quantidade REAL DEFAULT 1,
        valor_unitario REAL DEFAULT 0,
        observacao TEXT DEFAULT '',
        FOREIGN KEY(caminhao_id) REFERENCES caminhao_oficina(id)
    )''')
    try: cursor.execute("ALTER TABLE caminhao_oficina_itens ADD COLUMN observacao TEXT DEFAULT ''")
    except: pass

    # Conferências de estoque do caminhão oficina (auditoria física do que está faltando)
    cursor.execute('''CREATE TABLE IF NOT EXISTS caminhao_conferencias (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        caminhao_id INTEGER NOT NULL,
        data_conferencia TEXT NOT NULL,
        usuario TEXT DEFAULT '',
        observacao TEXT DEFAULT '',
        total_itens INTEGER DEFAULT 0,
        total_faltando INTEGER DEFAULT 0,
        criado_em TEXT NOT NULL,
        FOREIGN KEY(caminhao_id) REFERENCES caminhao_oficina(id)
    )''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_caminhao_conferencias_caminhao ON caminhao_conferencias(caminhao_id)')

    # Itens da conferência guardam uma cópia (código/descrição/qtd) do momento da checagem,
    # assim o histórico não muda retroativamente se o estoque cadastrado do caminhão for editado depois.
    cursor.execute('''CREATE TABLE IF NOT EXISTS caminhao_conferencia_itens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conferencia_id INTEGER NOT NULL,
        codigo TEXT DEFAULT '',
        descricao TEXT NOT NULL,
        quantidade_esperada REAL DEFAULT 0,
        presente INTEGER DEFAULT 1,
        observacao TEXT DEFAULT '',
        FOREIGN KEY(conferencia_id) REFERENCES caminhao_conferencias(id)
    )''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_caminhao_conf_itens_conferencia ON caminhao_conferencia_itens(conferencia_id)')

    # Auto-importa planilha de agregados se a tabela estiver vazia
    _xlsx_agregados = os.path.join(os.path.expanduser('~'), 'Downloads', 'Saldos Peças-Consertados.xlsx')
    if os.path.exists(_xlsx_agregados):
        try:
            total_agg = cursor.execute('SELECT COUNT(*) FROM ferramentaria_agregados').fetchone()[0]
            if total_agg == 0:
                import openpyxl as _openpyxl
                _wb = _openpyxl.load_workbook(_xlsx_agregados, data_only=True)
                _ws = _wb['CH570']
                _categoria = ''
                _item_num = 0
                _lote = []
                for _row in _ws.iter_rows(min_row=4, values_only=True):
                    _v0 = _row[0]
                    if _v0 is None and all(c is None for c in _row[1:]):
                        continue
                    # Linha de categoria: texto na col 0 e demais nulas
                    if isinstance(_v0, str) and _row[1] is None:
                        _categoria = str(_v0).strip().upper()
                        _item_num = 0
                        continue
                    if not isinstance(_v0, (int, float)):
                        continue
                    _item_num += 1
                    def _si(v):
                        s = str(v).strip() if v is not None else ''
                        return s if s not in ('None', 'AGUARDANDO CADASTRO', 'SOLICITANDO CADASTRO') else ''
                    def _ii(v):
                        try: return int(v) if v is not None else 0
                        except: return 0
                    _lote.append((
                        int(_v0), _categoria,
                        _si(_row[1]), _si(_row[2]),
                        _si(_row[4]), _si(_row[5]), _si(_row[6]),
                        _ii(_row[7]), _ii(_row[8]), _ii(_row[9]), _ii(_row[10]), _ii(_row[11]),
                        'CH570'
                    ))
                if _lote:
                    cursor.executemany('''INSERT INTO ferramentaria_agregados
                        (item, categoria, codigo_novo, codigo_recond, descricao, referencia, versao,
                         saldo_novo, p_conserto, saldo_recond, em_manut, devendo, modelo_maquina)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''', _lote)
                    print(f"Importados {len(_lote)} itens de agregados CH570.")
        except Exception as _e:
            print("Erro ao importar agregados:", _e)

    cursor.execute('''CREATE TABLE IF NOT EXISTS os_registros (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nro_os TEXT,
        status TEXT,
        data_abertura TEXT,
        veiculo TEXT,
        placa TEXT,
        descricao_veiculo TEXT,
        manutencao TEXT,
        centro_custo_nome TEXT,
        oficina_nome TEXT,
        tipo_os TEXT,
        solicitante_nome TEXT,
        data_liberacao TEXT,
        fundo_agricola TEXT,
        custo_oficina REAL DEFAULT 0,
        custo_mao_obra REAL DEFAULT 0,
        pecas_consumidas REAL DEFAULT 0,
        valor_os REAL DEFAULT 0,
        descricao_problema TEXT DEFAULT '',
        especialidade TEXT DEFAULT '',
        dias_aberta INTEGER DEFAULT 0
    )''')

    # O.S. Mensal + O.S. Abertas importadas na Programação Semanal, para o
    # classificador automático de Preventiva/Pit Stop. Tabela própria (não
    # reaproveita os_registros, que pertence a Serviços Externos e é apagada
    # por completo a cada upload de lá - misturar as duas quebraria uma
    # feature ao mexer na outra). nro_os como chave permite upsert e dedup
    # natural quando a mesma O.S. aparece nas duas bases.
    cursor.execute('''CREATE TABLE IF NOT EXISTS programacao_os_import (
        nro_os TEXT PRIMARY KEY,
        id_frota TEXT,
        veiculo_raw TEXT,
        status TEXT,
        tipo_classificado TEXT,
        manutencao_raw TEXT,
        descricao TEXT,
        data_abertura TEXT,
        data_liberacao TEXT,
        origem_planilha TEXT,
        atualizado_em TEXT
    )''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS frotas_terceiros (
        id_frota TEXT PRIMARY KEY,
        observacao TEXT DEFAULT ''
    )''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS sync_log (
        chave TEXT PRIMARY KEY,
        ultima_sync TEXT,
        total INTEGER DEFAULT 0
    )''')

    # Parâmetros do classificador. Ficam em tabela, e não espalhados pelo código,
    # justamente para trocar a janela de dias ou um termo de busca sem mexer aqui.
    cursor.execute('''CREATE TABLE IF NOT EXISTS pcm_parametros (
        chave TEXT PRIMARY KEY,
        valor TEXT,
        descricao TEXT,
        grupo TEXT
    )''')
    for _ch, _vl, _ds, _gr in [
        ('janela_antecipacao_preventiva', '7',
         'Dias antes da data programada em que uma OS preventiva ainda conta como realizada', 'Preventiva'),
        ('termos_preventiva', 'PREVENTIVA,PREVENTIVO,PREVENT',
         'Termos que identificam uma OS como preventiva (busca dentro da descrição)', 'Preventiva'),
        ('termos_pitstop', 'PITSTOP,PIT STOP',
         'Termos que identificam uma OS como pit stop (espaços são normalizados)', 'Pit Stop'),
        ('status_os_aberta', 'A', 'Código de status de OS aberta', 'O.S.'),
        ('status_os_execucao', 'E', 'Código de status de OS em execução', 'O.S.'),
        ('inicio_semana', '0', 'Dia que inicia a semana (0=segunda ... 6=domingo)', 'Pit Stop'),
    ]:
        cursor.execute(
            'INSERT OR IGNORE INTO pcm_parametros (chave, valor, descricao, grupo) VALUES (?,?,?,?)',
            (_ch, _vl, _ds, _gr))

    cursor.execute('''CREATE TABLE IF NOT EXISTS frentes_config (
        cod_frota TEXT PRIMARY KEY,
        frente    TEXT NOT NULL DEFAULT ''
    )''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS compras_solicitacoes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        data_solicitacao TEXT NOT NULL,
        fornecedor TEXT DEFAULT '',
        frota TEXT DEFAULT '',
        nro_orcamento TEXT DEFAULT '',
        nro_solicitacao TEXT DEFAULT '',
        descricao TEXT DEFAULT '',
        status TEXT DEFAULT 'ABERTO',
        prioridade TEXT DEFAULT 'NORMAL',
        criado_por TEXT DEFAULT '',
        criado_em TEXT NOT NULL,
        observacao TEXT DEFAULT '',
        data_conclusao TEXT
    )''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS compras_itens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        solicitacao_id INTEGER NOT NULL,
        codigo TEXT DEFAULT '',
        descricao TEXT NOT NULL,
        quantidade TEXT DEFAULT '',
        unidade TEXT DEFAULT '',
        FOREIGN KEY(solicitacao_id) REFERENCES compras_solicitacoes(id)
    )''')
    try: cursor.execute("ALTER TABLE compras_itens ADD COLUMN codigo TEXT DEFAULT ''")
    except: pass

    conn.commit()
    conn.close()

setup_db()

# ── Sync agendado com banco da empresa ────────────────────────────────────────
try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from db_empresa import sync_frotas, sync_os

    _scheduler = BackgroundScheduler(daemon=True)
    # Frotas: 1x por dia às 03:00
    _scheduler.add_job(sync_frotas, 'cron', hour=3,  minute=0,  id='job_sync_frotas')
    # OS: a cada hora no minuto :31
    _scheduler.add_job(sync_os,     'cron', minute=31,          id='job_sync_os')
    _scheduler.start()

    # Roda sync imediato na inicialização (em thread separada para não travar o boot)
    import threading
    threading.Thread(target=sync_frotas, daemon=True).start()
    threading.Thread(target=sync_os,     daemon=True).start()
except Exception as _e:
    print(f'[scheduler] Não inicializado: {_e}')

def is_admin_session():
    return bool(session.get('is_admin'))

def tem_acesso_modulo(modulo):
    if is_admin_session():
        return True
    chave_perm = {
        'bloqueios': 'acesso_bloqueios',
        'programacao': 'acesso_programacao',
        'lavagem': 'acesso_lavagem',
        'analises': 'acesso_analises',
        'abastecimentos': 'acesso_abastecimentos',
        'externos': 'acesso_externos',
        'ferramentaria': 'acesso_ferramentaria',
        'compras': 'acesso_compras'
    }.get(modulo)
    if not chave_perm:
        return False
    return bool(session.get(chave_perm, False))

def redirecionar_primeiro_modulo_permitido():
    if is_admin_session():
        return redirect(url_for('admin'))
    # Perfis comuns passam a usar o Admin como página inicial.
    if session.get('logado'):
        return redirect(url_for('admin'))
    ordem = [
        ('acesso_bloqueios', 'index'),
        ('acesso_programacao', 'programacao'),
        ('acesso_lavagem', 'lavagem'),
        ('acesso_analises', 'analises'),
        ('acesso_abastecimentos', 'abastecimentos'),
        ('acesso_externos', 'externos'),
        ('acesso_ferramentaria', 'ferramentaria')
    ]
    for chave, rota in ordem:
        if session.get(chave):
            return redirect(url_for(rota))
    flash('Este perfil não possui módulos liberados. Contate o administrador.', 'danger')
    return redirect(url_for('logout'))

@app.context_processor
def inject_acessos_usuario():
    return {
        'usuario_logado': bool(session.get('logado')),
        'usuario_admin': bool(session.get('is_admin')),
        'usuario_perfil_nome': session.get('perfil_nome', 'Usuário'),
        'perm_bloqueios': bool(session.get('acesso_bloqueios')),
        'perm_programacao': bool(session.get('acesso_programacao')),
        'perm_lavagem': bool(session.get('acesso_lavagem')),
        'perm_analises': bool(session.get('acesso_analises')),
        'perm_abastecimentos': bool(session.get('acesso_abastecimentos')),
        'perm_externos': bool(session.get('acesso_externos')),
        'perm_ferramentaria': bool(session.get('acesso_ferramentaria')),
        'perm_compras': bool(session.get('acesso_compras')),
        'compras_criar': bool(session.get('compras_criar', False)),
        'compras_editar': bool(session.get('compras_editar', False)),
        'compras_alterar_status': bool(session.get('compras_alterar_status', False)),
    }

def formatar_quantidade_ferramenta(valor):
    try:
        numero = float(valor or 0)
        return str(int(numero)) if numero.is_integer() else str(numero).replace('.', ',')
    except:
        return str(valor or '')

def get_ativos_agrupados(conn):
    ativos_bruto = conn.execute('''
        SELECT h.id, h.id_frota,
               COALESCE(NULLIF(TRIM(f.descricao), ''), 'SEM DESCRIÇÃO') as descricao,
               COALESCE(NULLIF(TRIM(f.agrupamento), ''), 'GERAL') as especialidade,
               h.motivo, h.data_bloqueio, h.status_oficina
        FROM historico_bloqueios h LEFT JOIN frotas f ON h.id_frota = f.id_frota
        WHERE h.data_desbloqueio IS NULL ORDER BY h.id ASC
    ''').fetchall()
    agrupado_frota = {}
    for row in ativos_bruto:
        frota = row['id_frota']
        if frota not in agrupado_frota:
            agrupado_frota[frota] = {'ids': str(row['id']), 'id_frota': frota, 'descricao': row['descricao'], 'especialidade': row['especialidade'], 'motivo': row['motivo'], 'data_bloqueio': row['data_bloqueio'], 'status_oficina': row['status_oficina']}
        else:
            agrupado_frota[frota]['ids'] += f",{row['id']}"
            agrupado_frota[frota]['motivo'] += f" + {row['motivo']}"
    agrupado_esp = {}
    for f in agrupado_frota.values():
        esp = f['especialidade']
        if esp not in agrupado_esp: agrupado_esp[esp] = []
        agrupado_esp[esp].append(f)
    return {k: v for k, v in sorted(agrupado_esp.items(), key=lambda item: (item[0] == 'GERAL', item[0]))}

def calcular_estatisticas(ativos_por_esp):
    todas_frotas = [f for lista in ativos_por_esp.values() for f in lista]
    total = len(todas_frotas)
    contagem = {}
    for item in todas_frotas:
        # Uma frota pode estar na oficina por varios motivos ("PREVENTIVA + PIT STOP").
        # Conta cada motivo, sem repetir o mesmo motivo duas vezes na mesma frota.
        vistos = set()
        for motivo in str(item['motivo'] or '').split(' + '):
            motivo = motivo.strip()
            if not motivo or motivo in vistos:
                continue
            vistos.add(motivo)
            contagem[motivo] = contagem.get(motivo, 0) + 1
    return total, contagem

def get_programacao_agrupada(conn, data_inicio_str, data_fim_str, sincronizar_preventiva=False):
    if sincronizar_preventiva:
        preventivas = conn.execute('''
            SELECT id_frota, MAX(data_planejada) as data_p
            FROM programacao_semanal
            WHERE tipo_servico LIKE '%PREVENTIVA%' AND data_planejada BETWEEN ? AND ?
            GROUP BY id_frota
        ''', (data_inicio_str, data_fim_str)).fetchall()

        for prev in preventivas:
            conn.execute('''
                UPDATE programacao_semanal
                SET data_planejada = ?
                WHERE id_frota = ?
                  AND data_planejada BETWEEN ? AND ?
                  AND COALESCE(NULLIF(TRIM(status), ''), 'PENDENTE') = 'PENDENTE'
                  AND (data_execucao IS NULL OR TRIM(data_execucao) = '')
                  AND tipo_servico NOT LIKE '%PREVENTIVA%'
                  AND tipo_servico NOT LIKE '%PIT STOP%'
                  AND tipo_servico NOT LIKE '%LAVAGEM%'
                  AND tipo_servico NOT LIKE '%LUBRIFICACAO%'
            ''', (prev['data_p'], prev['id_frota'], data_inicio_str, data_fim_str))
        conn.commit()

    raw_data = conn.execute('''
        SELECT p.id, p.id_frota,
               COALESCE(NULLIF(TRIM(f.descricao), ''), 'SEM DESCRIÇÃO') as descricao,
               COALESCE(NULLIF(TRIM(f.agrupamento), ''), 'GERAL') as especialidade,
               COALESCE(NULLIF(TRIM(f.especialidade), ''), 'OUTROS') as especialidade_detalhe,
               COALESCE(NULLIF(TRIM(f.setor), ''), 'SEM SETOR') as setor,
               p.tipo_servico, p.data_planejada, p.status,
               COALESCE(NULLIF(TRIM(p.local_execucao), ''), 'BASE') as local_execucao,
               IFNULL(p.resultado_analise, '') as resultado_analise,
               IFNULL(p.observacao, '') as observacao,
               IFNULL(p.os_vinculada, '') as os_vinculada,
               IFNULL(p.motivo_classificacao, '') as motivo_classificacao
        FROM programacao_semanal p
        LEFT JOIN frotas f ON p.id_frota = f.id_frota
        WHERE p.data_planejada BETWEEN ? AND ?
        ORDER BY p.data_planejada ASC
    ''', (data_inicio_str, data_fim_str)).fetchall()

    agrupado = {}
    for row in raw_data:
        ref_frota = dados_frota_referencia(conn, row['id_frota']) if re.search(r'\s*[-/]\s*|\s+\d', str(row['id_frota'] or '')) else None
        descricao = ref_frota['descricao'] if ref_frota else row['descricao']
        especialidade = ref_frota['especialidade'] if ref_frota else row['especialidade']
        especialidade_detalhe = ref_frota['especialidade_detalhe'] if ref_frota else row['especialidade_detalhe']
        setor = ref_frota['setor'] if ref_frota else row['setor']
        tipo_srv = (row['tipo_servico'] or '')
        if 'PIT STOP' in tipo_srv:
            grupo_chave = 'PIT_STOP'
        elif 'LAVAGEM' in tipo_srv or 'LUBRIFICACAO' in tipo_srv:
            grupo_chave = 'LAVAGEM'
        else:
            grupo_chave = 'GERAL'
        # Inclui local_execucao na chave para não mesclar serviços do mesmo dia/status
        # que foram executados em locais diferentes (ex.: BASE x GARANTIA).
        local_chave = (row['local_execucao'] or 'BASE').strip().upper()
        chave = f"{row['id_frota']}_{row['data_planejada']}_{row['status']}_{grupo_chave}_{local_chave}"
        if chave not in agrupado:
            agrupado[chave] = {
                'id_frota': row['id_frota'], 'descricao': descricao,
                'especialidade': especialidade,
                'especialidade_detalhe': especialidade_detalhe,
                'setor': setor,
                'data_planejada': row['data_planejada'],
                'status': row['status'],
                'local_execucao': row['local_execucao'],
                'servicos': [], 'resultados_analise': [], 'observacoes': []
            }
        agrupado[chave]['servicos'].append({
            'id': row['id'], 'tipo': row['tipo_servico'],
            'resultado': row['resultado_analise'], 'observacao': row['observacao'],
            'status': row['status'],
            'os_vinculada': row['os_vinculada'], 'motivo_classificacao': row['motivo_classificacao']
        })
        if row['resultado_analise']: agrupado[chave]['resultados_analise'].append(row['resultado_analise'])
        if row['observacao']: agrupado[chave]['observacoes'].append(row['observacao'])

    resultado = []
    for item in agrupado.values():
        item['ids'] = ",".join([str(s['id']) for s in item['servicos']])

        tipos_unicos = []
        for s in item['servicos']:
            if s['tipo'] not in tipos_unicos: tipos_unicos.append(s['tipo'])
        item['tipo_servico'] = " + ".join(tipos_unicos)

        def _sanitizar_modal(txt):
            return str(txt or '').replace(':', '').replace('|', '').replace(chr(39), '').replace(chr(34), '')
        item['dados_modal'] = "||".join([
            f"{s['id']}::{s['tipo']}::{s['resultado']}::{_sanitizar_modal(s['observacao'])}::{s['status']}::{_sanitizar_modal(s['os_vinculada'])}::{_sanitizar_modal(s['motivo_classificacao'])}"
            for s in item['servicos']
        ])

        obs_validas = [obs for obs in set(item['observacoes']) if obs.strip()]
        item['observacao_exibicao'] = " / ".join(obs_validas)

        res_validos = [res for res in set(item['resultados_analise']) if res.strip()]
        item['resultado_analise_exibicao'] = " / ".join(res_validos)

        resultado.append(item)
    return resultado

def calcular_stats_prog(programacao_db):
    stats_por_servico = {}
    for p in programacao_db:
        esp = p.get('especialidade', 'GERAL')
        frota = p.get('id_frota', 'N/A')

        for s in p.get('servicos', []):
            tipo = s.get('tipo', '').strip()
            if not tipo: continue
            status = s.get('status', 'PENDENTE')

            if tipo not in stats_por_servico:
                stats_por_servico[tipo] = {'total': 0, 'realizadas': 0, 'pendentes': 0, 'bloqueadas': 0, 'quebradas': 0, 'percentual': 0, 'especialidades': {}}

            st = stats_por_servico[tipo]
            st['total'] += 1
            if status == 'REALIZADA': st['realizadas'] += 1
            elif status == 'BLOQUEADA': st['bloqueadas'] += 1
            elif status == 'QUEBRADA': st['quebradas'] += 1
            else: st['pendentes'] += 1

            if esp not in st['especialidades']:
                st['especialidades'][esp] = {'total': 0, 'realizadas': 0, 'pendentes': 0, 'bloqueadas': 0, 'quebradas': 0, 'frotas_realizadas': [], 'frotas_pendentes': [], 'frotas_bloqueadas': [], 'frotas_quebradas': []}

            se = st['especialidades'][esp]
            se['total'] += 1
            if status == 'REALIZADA':
                se['realizadas'] += 1
                if frota not in se['frotas_realizadas']: se['frotas_realizadas'].append(frota)
            elif status == 'BLOQUEADA':
                se['bloqueadas'] += 1
                if frota not in se['frotas_bloqueadas']: se['frotas_bloqueadas'].append(frota)
            elif status == 'QUEBRADA':
                se['quebradas'] += 1
                if frota not in se['frotas_quebradas']: se['frotas_quebradas'].append(frota)
            else:
                se['pendentes'] += 1
                if frota not in se['frotas_pendentes']: se['frotas_pendentes'].append(frota)

    for tipo, dados in stats_por_servico.items():
        total_valido = dados['total'] - dados['quebradas']
        if total_valido > 0: dados['percentual'] = int((dados['realizadas'] / total_valido) * 100)
        else: dados['percentual'] = 0

    return dict(sorted(stats_por_servico.items()))

def get_resumo_dashboard_por_tipo(conn, data_inicio, data_fim, tipo_servico):
    programacao_db = get_programacao_agrupada(conn, data_inicio, data_fim)
    stats = calcular_stats_prog(programacao_db)
    return stats.get(tipo_servico, {
        'total': 0,
        'realizadas': 0,
        'pendentes': 0,
        'bloqueadas': 0,
        'quebradas': 0,
        'percentual': 0,
        'especialidades': {}
    })

def get_especialidades_da_semana(programacao_db):
    esp_set = set(p.get('especialidade', 'GERAL') for p in programacao_db)
    lista = sorted(list(esp_set))
    if 'GERAL' in lista:
        lista.remove('GERAL')
        lista.append('GERAL')
    return lista

def get_setores_da_semana(programacao_db):
    setor_set = set(p.get('setor', 'SEM SETOR') or 'SEM SETOR' for p in programacao_db)
    lista = sorted(list(setor_set))
    if 'SEM SETOR' in lista:
        lista.remove('SEM SETOR')
        lista.append('SEM SETOR')
    return lista

def get_dias_semana(segunda_alvo):
    dias_semana = []
    nomes_dias = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo']
    for i in range(7):
        dia = segunda_alvo + timedelta(days=i)
        dias_semana.append({ 'data_db': dia.strftime('%Y-%m-%d'), 'data_br': dia.strftime('%d/%m'), 'nome_dia': nomes_dias[i] })
    return dias_semana

def get_dias_do_mes(data_base):
    """Lista plana com todos os dias do mes (1 ao ultimo), para a visao mensal
    em faixa continua - mesmo formato de get_dias_semana."""
    nomes_dias = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo']
    primeiro_mes = data_base.replace(day=1)
    if primeiro_mes.month == 12:
        proximo_mes = primeiro_mes.replace(year=primeiro_mes.year + 1, month=1)
    else:
        proximo_mes = primeiro_mes.replace(month=primeiro_mes.month + 1)
    ultimo_mes = proximo_mes - timedelta(days=1)

    dias = []
    dia = primeiro_mes
    while dia <= ultimo_mes:
        dias.append({
            'data_db': dia.strftime('%Y-%m-%d'),
            'data_br': dia.strftime('%d/%m'),
            'dia_num': dia.day,
            'nome_dia': nomes_dias[dia.weekday()],
            'fim_semana': dia.weekday() >= 5
        })
        dia += timedelta(days=1)
    return dias

def get_calendario_mensal(data_base):
    nomes_dias = ['SEG', 'TER', 'QUA', 'QUI', 'SEX', 'SAB', 'DOM']
    primeiro_mes = data_base.replace(day=1)
    if primeiro_mes.month == 12:
        proximo_mes = primeiro_mes.replace(year=primeiro_mes.year + 1, month=1)
    else:
        proximo_mes = primeiro_mes.replace(month=primeiro_mes.month + 1)
    ultimo_mes = proximo_mes - timedelta(days=1)
    inicio_grade = primeiro_mes - timedelta(days=primeiro_mes.weekday())
    fim_grade = ultimo_mes + timedelta(days=(6 - ultimo_mes.weekday()))

    semanas = []
    semana = []
    dia = inicio_grade
    while dia <= fim_grade:
        semana.append({
            'data_db': dia.strftime('%Y-%m-%d'),
            'data_br': dia.strftime('%d/%m'),
            'dia_num': dia.day,
            'nome_dia': nomes_dias[dia.weekday()],
            'no_mes': dia.month == primeiro_mes.month
        })
        if len(semana) == 7:
            semanas.append(semana)
            semana = []
        dia += timedelta(days=1)
    return semanas

def primeira_frota_do_texto(valor):
    encontrados = re.findall(r'[A-Z]*\d+[A-Z0-9]*', str(valor or '').upper())
    return encontrados[0] if encontrados else str(valor or '').strip().upper()

def dados_frota_referencia(conn, id_frota):
    primeira = primeira_frota_do_texto(id_frota)
    if not primeira:
        return None
    return conn.execute('''
        SELECT COALESCE(NULLIF(TRIM(descricao), ''), 'SEM DESCRIÇÃO') as descricao,
               COALESCE(NULLIF(TRIM(agrupamento), ''), 'GERAL') as especialidade,
               COALESCE(NULLIF(TRIM(especialidade), ''), 'OUTROS') as especialidade_detalhe,
               COALESCE(NULLIF(TRIM(setor), ''), 'SEM SETOR') as setor
        FROM frotas
        WHERE id_frota = ?
    ''', (primeira,)).fetchone()

def extrair_grupos_frotas_lote(texto):
    grupos = []
    for linha in str(texto or '').replace(';', '\n').replace('\t', '\n').splitlines():
        partes = [p.strip().upper() for p in linha.split(',') if p.strip()]
        if not partes and linha.strip():
            partes = [linha.strip().upper()]
        for parte in partes:
            duplas = re.findall(r'([A-Z]*\d+[A-Z0-9]*)\s*-\s*([A-Z]*\d+[A-Z0-9]*)', parte)
            if duplas:
                for dupla in duplas:
                    grupos.append(list(dict.fromkeys(dupla)))
                continue

            frotas = re.findall(r'[A-Z]*\d+[A-Z0-9]*', parte)
            for frota in frotas:
                grupos.append([frota])
    return grupos

def aplicar_setor_primeira_frota_lote(conn, grupos_frotas):
    for grupo in grupos_frotas:
        if len(grupo) < 2:
            continue
        setor_base_row = conn.execute(
            "SELECT setor FROM frotas WHERE id_frota = ? AND setor IS NOT NULL AND TRIM(setor) != ''",
            (grupo[0],)
        ).fetchone()
        if not setor_base_row:
            continue
        setor_base = (setor_base_row['setor'] or '').strip().upper()
        if not setor_base:
            continue
        for frota in grupo[1:]:
            existente = conn.execute('SELECT id_frota, setor FROM frotas WHERE id_frota = ?', (frota,)).fetchone()
            if existente:
                conn.execute(
                    """
                    UPDATE frotas
                       SET setor = ?
                     WHERE id_frota = ?
                       AND (setor IS NULL OR TRIM(setor) = '')
                    """,
                    (setor_base, frota)
                )
            else:
                conn.execute(
                    'INSERT INTO frotas (id_frota, descricao, setor, especialidade, agrupamento) VALUES (?, ?, ?, ?, ?)',
                    (frota, 'SEM DESCRIÇÃO', setor_base, 'OUTROS', 'GERAL')
                )

@app.route('/api/dashboard_stats')
def dashboard_stats():
    inicio = request.args.get('inicio')
    fim = request.args.get('fim')
    modulo = (request.args.get('modulo') or 'programacao').strip().lower()
    if not inicio or not fim: return jsonify({})
    conn = get_db_connection()
    programacao_db = get_programacao_agrupada(conn, inicio, fim)
    if modulo == 'lavagem':
        programacao_db = [p for p in programacao_db if ('LAVAGEM' in (p.get('tipo_servico') or '') or 'LUBRIFICACAO' in (p.get('tipo_servico') or ''))]
    else:
        programacao_db = [p for p in programacao_db if ('LAVAGEM' not in (p.get('tipo_servico') or '') and 'LUBRIFICACAO' not in (p.get('tipo_servico') or ''))]
    stats = calcular_stats_prog(programacao_db)
    conn.close()
    return jsonify(stats)

@app.route('/')
def index():
    conn = get_db_connection()
    frotas_bloqueadas = get_ativos_agrupados(conn)
    offset = int(request.args.get('offset', 0))
    hoje_data = datetime.now()
    segunda_atual = hoje_data - timedelta(days=hoje_data.weekday())
    segunda_alvo = segunda_atual + timedelta(weeks=offset)
    domingo_alvo = segunda_alvo + timedelta(days=6)
    dias_semana = get_dias_semana(segunda_alvo)

    # Visao "Mes": mesma faixa continua de dias da Programacao Semanal, so que cobrindo o
    # mes inteiro (dia 1 ao ultimo), pra ficar consistente entre as duas telas.
    modo_visualizacao = (request.args.get('view') or 'semana').strip().lower()
    if modo_visualizacao not in ('semana', 'mensal'):
        modo_visualizacao = 'semana'
    mes_offset = int(request.args.get('mes_offset', 0))
    mes_base = hoje_data.replace(day=1)
    deslocamento_meses = (mes_base.month - 1) + mes_offset
    mes_alvo = mes_base.replace(
        year=mes_base.year + (deslocamento_meses // 12),
        month=(deslocamento_meses % 12) + 1
    )
    if mes_alvo.month == 12:
        proximo_mes = mes_alvo.replace(year=mes_alvo.year + 1, month=1)
    else:
        proximo_mes = mes_alvo.replace(month=mes_alvo.month + 1)
    ultimo_mes = proximo_mes - timedelta(days=1)
    dias_mes = get_dias_do_mes(mes_alvo)
    nomes_meses = ['Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho', 'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro']
    titulo_mes = f"{nomes_meses[mes_alvo.month - 1]} {mes_alvo.year}"

    if modo_visualizacao == 'mensal':
        data_inicio_str = mes_alvo.strftime('%Y-%m-%d')
        data_fim_str = ultimo_mes.strftime('%Y-%m-%d')
    else:
        data_inicio_str = segunda_alvo.strftime('%Y-%m-%d')
        data_fim_str = domingo_alvo.strftime('%Y-%m-%d')

    programacao_db = get_programacao_agrupada(conn, data_inicio_str, data_fim_str)
    stats_prog = calcular_stats_prog(programacao_db)
    especialidades_na_semana = get_especialidades_da_semana(programacao_db)
    setores_na_semana = get_setores_da_semana(programacao_db)
    conn.close()
    return render_template('index.html', frotas=frotas_bloqueadas, dias_semana=dias_semana, dias_mes=dias_mes,
                           modo_visualizacao=modo_visualizacao, mes_offset=mes_offset, titulo_mes=titulo_mes,
                           programacao=programacao_db, hoje=hoje_data.strftime('%Y-%m-%d'), offset=offset,
                           especialidades_na_semana=especialidades_na_semana, setores_na_semana=setores_na_semana,
                           data_inicio_db=data_inicio_str, data_fim_db=data_fim_str, stats=stats_prog)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        usuario = request.form.get('usuario', '').strip().upper()
        senha = request.form.get('senha', '')

        if usuario == 'ADMIN' and senha == 'admin':
            session['logado'] = True
            session['is_admin'] = True
            session['perfil_nome'] = 'ADMIN'
            return redirect(url_for('admin'))
        conn = get_db_connection()
        perfil = conn.execute('''
            SELECT * FROM perfis_acesso
            WHERE UPPER(TRIM(nome)) = ?
        ''', (usuario,)).fetchone()
        conn.close()

        if perfil and perfil['senha_hash'] and check_password_hash(perfil['senha_hash'], senha):
            session['logado'] = True
            session['is_admin'] = False
            session['perfil_nome'] = perfil['nome']
            session['acesso_bloqueios'] = bool(perfil['acesso_bloqueios'])
            session['acesso_programacao'] = bool(perfil['acesso_programacao'])
            session['acesso_lavagem'] = bool(perfil['acesso_lavagem'])
            session['acesso_analises'] = bool(perfil['acesso_analises'])
            session['acesso_abastecimentos'] = bool(perfil['acesso_abastecimentos'])
            session['acesso_externos'] = bool(perfil['acesso_externos'])
            session['acesso_ferramentaria'] = bool(perfil['acesso_ferramentaria'])
            session['acesso_compras'] = bool(perfil['acesso_compras'])
            session['compras_criar'] = bool(perfil['compras_criar'])
            session['compras_editar'] = bool(perfil['compras_editar'])
            session['compras_alterar_status'] = bool(perfil['compras_alterar_status'])
            return redirect(url_for('admin'))

        flash('Credenciais incorretas.', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    for chave in ['logado', 'is_admin', 'perfil_nome', 'acesso_bloqueios', 'acesso_programacao', 'acesso_lavagem', 'acesso_analises', 'acesso_abastecimentos', 'acesso_externos', 'acesso_ferramentaria', 'acesso_compras', 'compras_criar', 'compras_editar', 'compras_alterar_status']:
        session.pop(chave, None)
    return redirect(url_for('login'))

@app.route('/admin')
def admin():
    if not session.get('logado'): return redirect(url_for('login'))
    conn = get_db_connection()

    historico_bruto = conn.execute('''
        SELECT h.id, h.id_frota,
               COALESCE(NULLIF(TRIM(f.descricao), ''), 'SEM DESCRIÇÃO') as descricao,
               h.motivo, h.data_bloqueio, h.data_desbloqueio, h.status_oficina, h.observacao
        FROM historico_bloqueios h LEFT JOIN frotas f ON h.id_frota = f.id_frota
        ORDER BY h.id_frota ASC, h.id DESC
    ''').fetchall()

    frotas_cadastradas = conn.execute('''
        SELECT id_frota,
               COALESCE(NULLIF(TRIM(descricao), ''), 'SEM DESCRIÇÃO') as descricao,
               setor, status_abastecimento,
               COALESCE(NULLIF(TRIM(especialidade), ''), 'OUTROS') as especialidade,
               COALESCE(NULLIF(TRIM(agrupamento), ''), 'GERAL') as agrupamento
        FROM frotas ORDER BY id_frota
    ''').fetchall()

    agrupamentos_unicos = conn.execute('SELECT DISTINCT agrupamento FROM frotas WHERE agrupamento IS NOT NULL AND TRIM(agrupamento) != "" ORDER BY agrupamento').fetchall()
    especialidades_unicas = conn.execute('SELECT DISTINCT especialidade FROM frotas WHERE especialidade IS NOT NULL AND TRIM(especialidade) != "" ORDER BY especialidade').fetchall()
    setores_unicos = conn.execute('SELECT DISTINCT setor FROM frotas WHERE setor IS NOT NULL AND TRIM(setor) != "" ORDER BY setor').fetchall()
    perfis = conn.execute('SELECT * FROM perfis_acesso ORDER BY nome').fetchall() if is_admin_session() else []

    ativos = get_ativos_agrupados(conn)
    conn.close()

    historico_agrupado = {}
    # Versão serializável do mesmo histórico, usada pelo modal de detalhes da frota.
    detalhes_frotas = {}
    for row in historico_bruto:
        frota = row['id_frota']
        if frota not in historico_agrupado: historico_agrupado[frota] = {'descricao': row['descricao'], 'eventos': []}
        historico_agrupado[frota]['eventos'].append(row)

        if frota not in detalhes_frotas:
            detalhes_frotas[frota] = {'descricao': row['descricao'], 'eventos': []}
        detalhes_frotas[frota]['eventos'].append({
            'motivo': row['motivo'] or '',
            'entrada': row['data_bloqueio'] or '',
            'saida': row['data_desbloqueio'] or '',
            'status': row['status_oficina'] or '',
            'obs': row['observacao'] or '',
            'aberto': not row['data_desbloqueio'],
        })
    total_bloqueadas, contagem_motivos = calcular_estatisticas(ativos)
    return render_template('admin.html', ativos=ativos, historico_agrupado=historico_agrupado, total=total_bloqueadas, contagem=contagem_motivos, frotas_cadastradas=frotas_cadastradas, especialidades_unicas=especialidades_unicas, agrupamentos_unicos=agrupamentos_unicos, setores_unicos=setores_unicos, perfis=perfis, detalhes_frotas=detalhes_frotas)

# ── Classificador de Preventiva / Pit Stop a partir das O.S. ─────────────────
def ler_parametros(conn):
    """Parâmetros do classificador, com padrão caso a linha ainda não exista."""
    padrao = {
        'janela_antecipacao_preventiva': '7',
        'termos_preventiva': 'PREVENTIVA,PREVENTIVO,PREVENT',
        'termos_pitstop': 'PITSTOP,PIT STOP',
        'status_os_aberta': 'A',
        'status_os_execucao': 'E',
        'inicio_semana': '0',
    }
    for r in conn.execute('SELECT chave, valor FROM pcm_parametros').fetchall():
        padrao[r['chave']] = r['valor']
    return padrao


def _texto_os(txt):
    """Normaliza para comparação: sem acento, maiúsculo e espaços colapsados.
    É o que faz 'PIT  STOP' e 'Pit Stop' caírem no mesmo termo."""
    import unicodedata
    t = str(txt or '').upper()
    t = unicodedata.normalize('NFKD', t)
    t = ''.join(c for c in t if not unicodedata.combining(c))
    return ' '.join(t.split())


def _classificar_tipo_os(descricao, manutencao, termos_prev, termos_pit):
    """Diz se a OS é preventiva, pit stop ou outra coisa. O pit stop é checado
    antes porque descrições como 'REALIZADO PIT STOP - TROCA DE OLEO' também
    poderiam casar com outros termos."""
    alvo = _texto_os(descricao) + ' ' + _texto_os(manutencao)
    alvo_sem_espaco = alvo.replace(' ', '')
    for t in termos_pit:
        if t and (t in alvo or t.replace(' ', '') in alvo_sem_espaco):
            return 'PITSTOP'
    for t in termos_prev:
        if t and t in alvo:
            return 'PREVENTIVA'
    return 'OUTRA'


def classificar_programacao(conn, data_inicio, data_fim, fazer_preventiva=True, fazer_pitstop=True):
    """Compara a programação do período com as O.S. importadas e devolve, para
    cada serviço programado, o status resultante.

    Preventiva trabalha por ciclo com janela de antecipação: uma O.S. encerrada
    até N dias antes da data programada já cobre aquele ciclo, e uma O.S. que
    fica aberta vários dias cobre todas as datas dentro da sua vigência.

    Pit Stop trabalha por semana fechada: a O.S. precisa tocar a semana da
    programação. Uma O.S. que abre numa semana e encerra na seguinte vale para
    as duas, porque o intervalo atravessa a virada.
    """
    p = ler_parametros(conn)
    janela = int(p.get('janela_antecipacao_preventiva') or 7)
    termos_prev = [_texto_os(t) for t in (p.get('termos_preventiva') or '').split(',') if t.strip()]
    termos_pit = [_texto_os(t) for t in (p.get('termos_pitstop') or '').split(',') if t.strip()]
    st_aberta = (p.get('status_os_aberta') or 'A').strip().upper()
    st_exec = (p.get('status_os_execucao') or 'E').strip().upper()
    ini_semana = int(p.get('inicio_semana') or 0)

    def d(txt):
        try:
            return datetime.strptime(str(txt)[:10], '%Y-%m-%d').date()
        except Exception:
            return None

    # O.S. da frota, com tipo e intervalo de vigência já resolvidos. Vem de
    # programacao_os_import (O.S. Mensal + O.S. Abertas importadas na própria
    # Programação Semanal), não de os_registros (que é de Serviços Externos).
    os_por_frota = {}
    for r in conn.execute('''
        SELECT id_frota, status, manutencao_raw, data_abertura, data_liberacao,
               descricao, nro_os
        FROM programacao_os_import
    ''').fetchall():
        frota = str(r['id_frota'] or '').strip().upper()
        if not frota:
            continue
        abertura = d(r['data_abertura'])
        if not abertura:
            continue
        liberacao = d(r['data_liberacao'])
        status = str(r['status'] or '').strip().upper()
        em_aberto = status in (st_aberta, st_exec)
        os_por_frota.setdefault(frota, []).append({
            'nro': r['nro_os'],
            'tipo': _classificar_tipo_os(r['descricao'], r['manutencao_raw'], termos_prev, termos_pit),
            'abertura': abertura,
            'liberacao': liberacao,
            'em_aberto': em_aberto,
            # Enquanto não há liberação, a O.S. segue vigente até hoje.
            'fim_vigencia': liberacao or (datetime.now().date() if em_aberto else abertura),
        })

    hoje = datetime.now().date()
    resultados = []

    # Um bloqueio manual (status BLOQUEADA) nunca é sobrescrito pela
    # classificação automática - fica de fora da reavaliação por completo.
    programados = conn.execute('''
        SELECT p.id, p.id_frota, p.tipo_servico, p.data_planejada, p.status
        FROM programacao_semanal p
        WHERE p.data_planejada BETWEEN ? AND ?
          AND COALESCE(NULLIF(TRIM(p.status), ''), 'PENDENTE') != 'BLOQUEADA'
    ''', (data_inicio, data_fim)).fetchall()

    for prog in programados:
        alvo = _texto_os(prog['tipo_servico'])
        eh_pit = any(t.replace(' ', '') in alvo.replace(' ', '') for t in termos_pit)
        eh_prev = (not eh_pit) and any(t in alvo for t in termos_prev)
        if eh_pit and not fazer_pitstop:
            continue
        if eh_prev and not fazer_preventiva:
            continue
        if not (eh_pit or eh_prev):
            continue  # só preventiva e pit stop entram nesta classificação

        data_prog = d(prog['data_planejada'])
        if not data_prog:
            continue

        frota = str(prog['id_frota'] or '').strip().upper()
        lista = os_por_frota.get(frota, [])
        tipo_alvo = 'PITSTOP' if eh_pit else 'PREVENTIVA'
        candidatas = [o for o in lista if o['tipo'] == tipo_alvo]

        novo_status, ref, motivo = None, None, ''

        if eh_prev:
            # 1) O.S. cuja vigência cobre a data programada
            cobre = [o for o in candidatas if o['abertura'] <= data_prog <= o['fim_vigencia']]
            # 2) ou encerrada dentro da janela de antecipação
            antecipada = [o for o in candidatas
                          if o['fim_vigencia'] < data_prog
                          and (data_prog - o['fim_vigencia']).days <= janela]
            atrasada = [o for o in candidatas if o['abertura'] > data_prog]

            if cobre:
                novo_status, ref = 'REALIZADA', cobre[0]
                motivo = 'O.S. preventiva vigente na data programada'
            elif antecipada:
                novo_status, ref = 'REALIZADA', antecipada[0]
                motivo = f'O.S. preventiva encerrada até {janela} dias antes da programação'
            elif atrasada:
                novo_status, ref = 'REALIZADA', atrasada[0]
                motivo = 'O.S. preventiva aberta depois da data programada'
        else:
            # Pit stop: a O.S. precisa tocar a semana da programação.
            desloc = (data_prog.weekday() - ini_semana) % 7
            ini_sem = data_prog - timedelta(days=desloc)
            fim_sem = ini_sem + timedelta(days=6)
            # Intervalo da O.S. cruza a semana - cobre inclusive a virada.
            na_semana = [o for o in candidatas
                         if o['abertura'] <= fim_sem and o['fim_vigencia'] >= ini_sem]
            if na_semana:
                ref = na_semana[0]
                novo_status = 'REALIZADA'
                motivo = ('Pit stop na semana da programação'
                          if ref['abertura'] <= data_prog
                          else 'Pit stop aberto depois do dia programado, na mesma semana')

        if novo_status is None:
            # Sem O.S. do tipo: se há outra manutenção em aberto, a frota está
            # parada. Reaproveita o status QUEBRADA (amarelo) já existente em
            # vez de criar um status novo - não muda aderência, cores nem
            # filtros em nenhum outro lugar do sistema.
            outra_aberta = [o for o in lista if o['em_aberto'] and o['tipo'] != tipo_alvo]
            if outra_aberta:
                novo_status, ref = 'QUEBRADA', outra_aberta[0]
                motivo = f'O.S. nº {outra_aberta[0]["nro"]} de manutenção em aberto - {tipo_alvo.lower()} não localizado(a) para este ciclo'
            elif data_prog <= hoje:
                novo_status = 'PENDENTE'
                motivo = 'Sem O.S. localizada para o ciclo'
            else:
                continue  # data futura: nada a alterar ainda

        atrasado = bool(ref and ref['abertura'] > data_prog and novo_status == 'REALIZADA')
        resultados.append({
            'id': prog['id'],
            'frota': frota,
            'tipo': tipo_alvo,
            'data_planejada': prog['data_planejada'],
            'status_atual': prog['status'],
            'status_novo': novo_status,
            'com_atraso': atrasado,
            'os': ref['nro'] if ref else None,
            'os_abertura': ref['abertura'].strftime('%d/%m/%Y') if ref else None,
            'os_liberacao': ref['liberacao'].strftime('%d/%m/%Y') if ref and ref['liberacao'] else None,
            'motivo': motivo,
        })

    return resultados


def montar_visao_operacional(conn):
    """Indicadores que cruzam oficina, programação da semana, ferramentaria e
    compras. Vive em rota própria (/dash_operacional) pra manter o Painel limpo."""
    ativos = get_ativos_agrupados(conn)
    hoje = datetime.now()
    segunda = hoje - timedelta(days=hoje.weekday())
    domingo = segunda + timedelta(days=6)

    stats_semana = calcular_stats_prog(
        get_programacao_agrupada(conn, segunda.strftime('%Y-%m-%d'), domingo.strftime('%Y-%m-%d'))
    )
    sem = {'total': 0, 'realizadas': 0, 'pendentes': 0, 'bloqueadas': 0, 'quebradas': 0}
    for _tipo, d in stats_semana.items():
        for k in sem:
            sem[k] += d[k]
    valido = sem['total'] - sem['quebradas']

    # ativos vem agrupado por especialidade ({esp: [frotas]}): o total de frotas
    # bloqueadas é a soma das listas, não o len() do dicionário.
    bloqueadas = sum(len(lista) for lista in ativos.values())
    total_frotas = conn.execute('SELECT COUNT(*) as n FROM frotas').fetchone()['n']
    ferr_aberto = conn.execute(
        'SELECT COUNT(*) as n FROM ferramentaria_retiradas WHERE data_devolucao IS NULL'
    ).fetchone()['n']
    compras_abertas = conn.execute('''
        SELECT COUNT(*) as n FROM compras_solicitacoes
        WHERE status NOT IN ('COMPRADO', 'CANCELADO')
    ''').fetchone()['n']
    atrasadas = conn.execute('''
        SELECT COUNT(*) as n FROM programacao_semanal
        WHERE data_planejada < ?
          AND COALESCE(NULLIF(TRIM(status), ''), 'PENDENTE') IN ('PENDENTE', 'BLOQUEADA')
    ''', (hoje.strftime('%Y-%m-%d'),)).fetchone()['n']
    ferr_longa = conn.execute('''
        SELECT COUNT(*) as n FROM ferramentaria_retiradas
        WHERE data_devolucao IS NULL
          AND julianday('now') - julianday(data_retirada) > 30
    ''').fetchone()['n']

    visao = {
        'total_frotas': total_frotas,
        'bloqueadas': bloqueadas,
        'disponiveis': max(total_frotas - bloqueadas, 0),
        'disponibilidade_pct': round(((total_frotas - bloqueadas) / total_frotas) * 100, 1) if total_frotas else 0,
        'semana_total': sem['total'],
        'semana_realizadas': sem['realizadas'],
        'semana_pendentes': sem['pendentes'] + sem['bloqueadas'],
        'semana_quebradas': sem['quebradas'],
        'semana_aderencia': round((sem['realizadas'] / valido) * 100, 1) if valido > 0 else 0,
        'periodo': f"{segunda.strftime('%d/%m')} a {domingo.strftime('%d/%m')}",
        'ferramentas_em_posse': ferr_aberto,
        'compras_abertas': compras_abertas,
    }

    # Aderência das últimas 8 semanas fechadas + a atual: alimenta o sparkline
    # de fundo do card (ideia trazida da bklit-ui, refeita em Chart.js puro).
    tendencia_aderencia = []
    for i in range(7, -1, -1):
        seg_i = segunda - timedelta(weeks=i)
        dom_i = seg_i + timedelta(days=6)
        r = conn.execute('''
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN status = 'REALIZADA' THEN 1 ELSE 0 END) as realizadas,
                   SUM(CASE WHEN status = 'QUEBRADA'  THEN 1 ELSE 0 END) as quebradas
            FROM programacao_semanal WHERE data_planejada BETWEEN ? AND ?
        ''', (seg_i.strftime('%Y-%m-%d'), dom_i.strftime('%Y-%m-%d'))).fetchone()
        val = (r['total'] or 0) - (r['quebradas'] or 0)
        ader_i = round((r['realizadas'] / val) * 100, 1) if val > 0 else None
        if ader_i is not None:
            tendencia_aderencia.append({'label': seg_i.strftime('%d/%m'), 'valor': ader_i})
    visao['tendencia_aderencia'] = tendencia_aderencia

    # Só entram alertas que realmente têm ocorrência.
    alertas = []
    if atrasadas:
        alertas.append({'nivel': 'bloqueada', 'icone': 'bi-clock-history', 'titulo': 'Atividades atrasadas',
                        'qtd': atrasadas, 'texto': 'programadas para datas já passadas e ainda sem execução',
                        'link': '/programacao'})
    if bloqueadas:
        alertas.append({'nivel': 'bloqueada', 'icone': 'bi-tools', 'titulo': 'Frotas bloqueadas',
                        'qtd': bloqueadas, 'texto': 'paradas na oficina agora', 'link': '/admin'})
    if sem['quebradas']:
        alertas.append({'nivel': 'quebrada', 'icone': 'bi-cone-striped', 'titulo': 'Frotas quebradas',
                        'qtd': sem['quebradas'], 'texto': 'na programação desta semana', 'link': '/programacao'})
    if visao['semana_pendentes']:
        alertas.append({'nivel': 'pendente', 'icone': 'bi-hourglass-split', 'titulo': 'A executar nesta semana',
                        'qtd': visao['semana_pendentes'], 'texto': 'atividades ainda não concluídas',
                        'link': '/programacao'})
    if ferr_longa:
        alertas.append({'nivel': 'atrasada', 'icone': 'bi-hammer', 'titulo': 'Ferramentas há mais de 30 dias',
                        'qtd': ferr_longa, 'texto': 'retiradas sem devolução registrada',
                        'link': '/ferramentaria?aba=historico'})
    if compras_abertas:
        alertas.append({'nivel': 'atrasada', 'icone': 'bi-cart', 'titulo': 'Compras em andamento',
                        'qtd': compras_abertas, 'texto': 'solicitações ainda não finalizadas', 'link': '/compras'})

    return visao, alertas, ativos, stats_semana


@app.route('/api/drilldown')
def api_drilldown():
    """Devolve a lista de frotas/serviços por trás de um número de KPI.

    Cada card do sistema que quiser 'abrir e ver quais itens compõem esse
    valor' (Foto 6 do pedido) chama PCM.drilldown('/api/drilldown?tipo=...'),
    e este endpoint centraliza as consultas em vez de cada tela reimplementar
    a própria lista. `tipo` escolhe qual recorte mostrar.
    """
    if not session.get('logado'):
        return jsonify({'erro': 'nao autenticado'}), 401

    tipo = (request.args.get('tipo') or '').strip()
    conn = get_db_connection()
    hoje = datetime.now()

    def item(frota, descricao, situacao, cor=None):
        return {'frota': frota, 'descricao': descricao, 'situacao': situacao, 'cor': cor}

    itens, titulo, subtitulo = [], tipo, ''

    if tipo == 'frotas_bloqueadas':
        titulo, subtitulo = 'Frotas bloqueadas', 'Paradas na oficina agora'
        ativos = get_ativos_agrupados(conn)
        for lista in ativos.values():
            for f in lista:
                itens.append(item(f['id_frota'], f['descricao'], f['motivo'], 'bloqueada'))

    elif tipo == 'frotas_disponiveis':
        titulo, subtitulo = 'Frotas disponíveis', 'Não bloqueadas na oficina'
        bloqueadas_ids = {r['id_frota'] for r in conn.execute(
            "SELECT DISTINCT id_frota FROM historico_bloqueios WHERE data_desbloqueio IS NULL")}
        rows = conn.execute('''SELECT id_frota, COALESCE(NULLIF(TRIM(descricao),''),'SEM DESCRIÇÃO') d,
                                       COALESCE(NULLIF(TRIM(especialidade),''),'OUTROS') e FROM frotas''')
        for r in rows:
            if r['id_frota'] not in bloqueadas_ids:
                itens.append(item(r['id_frota'], r['d'], r['e'], 'realizada'))

    elif tipo in ('semana_pendentes', 'semana_realizadas', 'semana_quebradas', 'semana_atrasadas'):
        segunda = hoje - timedelta(days=hoje.weekday())
        domingo = segunda + timedelta(days=6)
        if tipo == 'semana_atrasadas':
            titulo, subtitulo = 'Atividades atrasadas', 'Programadas para datas já passadas, ainda sem execução'
            where = "p.data_planejada < ? AND COALESCE(NULLIF(TRIM(p.status),''),'PENDENTE') IN ('PENDENTE','BLOQUEADA')"
            args = (hoje.strftime('%Y-%m-%d'),)
        else:
            args = (segunda.strftime('%Y-%m-%d'), domingo.strftime('%Y-%m-%d'))
            if tipo == 'semana_pendentes':
                titulo, subtitulo = 'A executar nesta semana', 'Pendentes ou bloqueadas'
                where = "p.data_planejada BETWEEN ? AND ? AND COALESCE(NULLIF(TRIM(p.status),''),'PENDENTE') IN ('PENDENTE','BLOQUEADA')"
            elif tipo == 'semana_realizadas':
                titulo, subtitulo = 'Realizadas nesta semana', ''
                where = "p.data_planejada BETWEEN ? AND ? AND p.status = 'REALIZADA'"
            else:
                titulo, subtitulo = 'Quebradas nesta semana', 'Fora do cálculo de aderência'
                where = "p.data_planejada BETWEEN ? AND ? AND p.status = 'QUEBRADA'"
        rows = conn.execute(f'''
            SELECT p.id_frota, p.tipo_servico, p.data_planejada,
                   COALESCE(NULLIF(TRIM(p.status),''),'PENDENTE') as status,
                   COALESCE(NULLIF(TRIM(f.descricao),''),'') as descricao
            FROM programacao_semanal p LEFT JOIN frotas f ON f.id_frota = p.id_frota
            WHERE {where} ORDER BY p.data_planejada, p.id_frota
        ''', args)
        for r in rows:
            desc = f"{r['tipo_servico']} · {r['descricao']}" if r['descricao'] else r['tipo_servico']
            itens.append(item(r['id_frota'], desc, r['status']))

    elif tipo == 'ferramentas_em_posse':
        titulo, subtitulo = 'Ferramentas em posse', 'Retiradas sem devolução registrada'
        rows = conn.execute('''SELECT mecanico, frota, local, data_retirada
                               FROM ferramentaria_retiradas WHERE data_devolucao IS NULL
                               ORDER BY data_retirada''')
        for r in rows:
            dias = (hoje.date() - datetime.strptime(r['data_retirada'], '%Y-%m-%d').date()).days if r['data_retirada'] else 0
            cor = 'atrasada' if dias > 30 else 'pendente'
            itens.append(item(r['mecanico'] or '—', f"{r['frota'] or r['local'] or ''} · {dias} dia(s)", f'{dias}d em posse', cor))

    elif tipo == 'compras_abertas':
        titulo, subtitulo = 'Compras em andamento', 'Ainda não finalizadas'
        rows = conn.execute('''SELECT frota, descricao, fornecedor, status, data_solicitacao
                               FROM compras_solicitacoes WHERE status NOT IN ('COMPRADO','CANCELADO')
                               ORDER BY data_solicitacao''')
        for r in rows:
            itens.append(item(r['frota'] or '—', f"{r['descricao'] or ''} · {r['fornecedor'] or ''}", r['status']))

    else:
        conn.close()
        return jsonify({'erro': 'tipo desconhecido'}), 400

    conn.close()
    return jsonify({'titulo': titulo, 'subtitulo': subtitulo, 'itens': itens})


@app.route('/dash_operacional')
def dash_operacional():
    if not session.get('logado'):
        return redirect(url_for('login'))

    conn = get_db_connection()
    visao, alertas, ativos, stats_semana = montar_visao_operacional(conn)

    # Bloqueios por especialidade e por motivo: onde a oficina está represada.
    por_especialidade = sorted(
        [{'nome': esp, 'qtd': len(lista)} for esp, lista in ativos.items()],
        key=lambda x: x['qtd'], reverse=True
    )
    _total_bloq, contagem_motivos = calcular_estatisticas(ativos)
    por_motivo = sorted(
        [{'nome': m, 'qtd': q} for m, q in contagem_motivos.items()],
        key=lambda x: x['qtd'], reverse=True
    )

    # Aderência por tipo de serviço na semana - mesma regra do resto do sistema.
    por_tipo = []
    for tipo, d in stats_semana.items():
        val = d['total'] - d['quebradas']
        por_tipo.append({
            'nome': tipo, 'total': d['total'], 'realizadas': d['realizadas'],
            'pendentes': d['pendentes'], 'bloqueadas': d['bloqueadas'], 'quebradas': d['quebradas'],
            'aderencia': round((d['realizadas'] / val) * 100, 1) if val > 0 else 0
        })
    por_tipo.sort(key=lambda x: x['aderencia'])

    # Frotas com mais passagens pela oficina no ano - candidatas a análise de causa.
    reincidentes = [dict(r) for r in conn.execute('''
        SELECT h.id_frota,
               COALESCE(NULLIF(TRIM(f.descricao), ''), 'SEM DESCRIÇÃO') as descricao,
               COUNT(*) as passagens,
               SUM(CASE WHEN h.data_desbloqueio IS NULL THEN 1 ELSE 0 END) as em_aberto
        FROM historico_bloqueios h
        LEFT JOIN frotas f ON f.id_frota = h.id_frota
        GROUP BY h.id_frota
        HAVING passagens > 1
        ORDER BY passagens DESC, em_aberto DESC
        LIMIT 12
    ''').fetchall()]
    conn.close()

    return render_template('dash_operacional.html',
                           visao_geral=visao, alertas=alertas,
                           por_especialidade=por_especialidade, por_motivo=por_motivo,
                           por_tipo=por_tipo, reincidentes=reincidentes)


def normalizar_chave_chb(valor):
    import unicodedata
    txt = str(valor or '').strip().upper()
    txt = unicodedata.normalize('NFKD', txt)
    txt = ''.join(ch for ch in txt if not unicodedata.combining(ch))
    return ' '.join(txt.split())

def obter_valor_chb(mapa, chaves):
    for chave in chaves:
        v = mapa.get(chave)
        if v is not None and str(v).strip() != '':
            return str(v).strip()
    return ''

def parse_data_execucao_chb(valor):
    txt = str(valor or '').strip()
    if not txt:
        return ''
    formatos = ['%Y-%m-%d %H:%M:%S', '%Y-%m-%d', '%d/%m/%Y %H:%M:%S', '%d/%m/%Y']
    for fmt in formatos:
        try:
            return datetime.strptime(txt[:19], fmt).strftime('%Y-%m-%d')
        except:
            pass
    return txt[:10] if len(txt) >= 10 else txt

def parse_hora_chb(valor):
    txt = str(valor or '').strip()
    if not txt:
        return ''
    formatos = ['%H:%M:%S', '%H:%M']
    for fmt in formatos:
        try:
            return datetime.strptime(txt[:8], fmt).strftime('%H:%M:%S')
        except:
            pass
    if ' ' in txt:
        parte = txt.split(' ')[-1]
        for fmt in formatos:
            try:
                return datetime.strptime(parte[:8], fmt).strftime('%H:%M:%S')
            except:
                pass
    return ''

def turno_por_hora(hora_txt):
    if not hora_txt:
        return 'N/I'
    try:
        h, m, _ = hora_txt.split(':')
        minutos = int(h) * 60 + int(m)
    except:
        return 'N/I'
    if 420 <= minutos < 920:
        return 'A'
    if 920 <= minutos < 1400:
        return 'B'
    return 'C'

def duracao_minutos(hora_ini, hora_fim):
    if not hora_ini or not hora_fim:
        return 0
    try:
        hi = datetime.strptime(hora_ini, '%H:%M:%S')
        hf = datetime.strptime(hora_fim, '%H:%M:%S')
    except:
        return 0
    mins = int((hf - hi).total_seconds() // 60)
    if mins < 0:
        mins += 24 * 60
    return max(mins, 0)

def gerar_stats_turno_chb(registros):
    turnos_base = {
        'A': {'nome': 'Turno A (07:00 - 15:20)', 'total': 0, 'mecanicos': set(), 'servicos': set(), 'tempo': 0},
        'B': {'nome': 'Turno B (15:20 - 23:20)', 'total': 0, 'mecanicos': set(), 'servicos': set(), 'tempo': 0},
        'C': {'nome': 'Turno C (23:20 - 07:20)', 'total': 0, 'mecanicos': set(), 'servicos': set(), 'tempo': 0},
        'N/I': {'nome': 'Sem Turno Identificado', 'total': 0, 'mecanicos': set(), 'servicos': set(), 'tempo': 0},
    }

    cont_mecanicos = Counter()
    cont_servicos = Counter()
    datas = []
    cont_dias = Counter()

    for r in registros:
        turno = r.get('turno', 'N/I')
        if turno not in turnos_base:
            turno = 'N/I'
        turnos_base[turno]['total'] += 1
        nome_mec = (r.get('nome_mecanico') or '').strip() or (r.get('mecanico') or '').strip() or 'SEM MECÂNICO'
        serv_desc = (r.get('descricao_servico') or '').strip() or (r.get('servico') or '').strip() or 'SEM SERVIÇO'
        turnos_base[turno]['mecanicos'].add(nome_mec)
        turnos_base[turno]['servicos'].add(serv_desc)
        turnos_base[turno]['tempo'] += duracao_minutos(r.get('hora_inicial', ''), r.get('hora_final', ''))
        cont_mecanicos[nome_mec] += 1
        cont_servicos[serv_desc] += 1

        data_exec = (r.get('data_execucao') or '').strip()
        if data_exec:
            datas.append(data_exec)
            cont_dias[data_exec] += 1

    totais_turno = {k: v['total'] for k, v in turnos_base.items() if k != 'N/I'}
    turno_campeao = max(totais_turno, key=totais_turno.get) if any(totais_turno.values()) else 'N/I'

    comparativo = []
    for chave in ['A', 'B', 'C', 'N/I']:
        d = turnos_base[chave]
        comparativo.append({
            'turno': chave,
            'nome': d['nome'],
            'total': d['total'],
            'mecanicos_unicos': len(d['mecanicos']),
            'servicos_unicos': len(d['servicos']),
            'tempo_min': d['tempo'],
            'tempo_horas': round(d['tempo'] / 60, 1)
        })

    periodo = 'Sem dados'
    periodo_inicio = ''
    periodo_fim = ''
    periodo_dias = 0
    if datas:
        try:
            dt_min = min(datetime.strptime(d, '%Y-%m-%d') for d in datas)
            dt_max = max(datetime.strptime(d, '%Y-%m-%d') for d in datas)
            periodo = f"{dt_min.strftime('%d/%m/%Y')} a {dt_max.strftime('%d/%m/%Y')}"
            periodo_inicio = dt_min.strftime('%d/%m/%Y')
            periodo_fim = dt_max.strftime('%d/%m/%Y')
            periodo_dias = (dt_max - dt_min).days + 1
        except:
            pass

    return {
        'total_registros': len(registros),
        'mecanicos_unicos': len(set((r.get('nome_mecanico') or '').strip() or (r.get('mecanico') or '').strip() or 'SEM MECÂNICO' for r in registros)),
        'turno_campeao': turno_campeao,
        'turno_campeao_nome': turnos_base.get(turno_campeao, {}).get('nome', 'Sem dados'),
        'tempo_total_horas': round(sum(d['tempo'] for d in turnos_base.values()) / 60, 1),
        'periodo': periodo,
        'periodo_inicio': periodo_inicio,
        'periodo_fim': periodo_fim,
        'periodo_dias': periodo_dias,
        'comparativo': comparativo,
        'top_mecanicos': [{'nome': n, 'total': t} for n, t in cont_mecanicos.most_common(10)],
        'top_servicos': [{'nome': n, 'total': t} for n, t in cont_servicos.most_common(10)],
        'grafico_turnos': [turnos_base['A']['total'], turnos_base['B']['total'], turnos_base['C']['total']],
        'grafico_turnos_labels': ['Turno A', 'Turno B', 'Turno C'],
        'grafico_dias_labels': [
            datetime.strptime(d, '%Y-%m-%d').strftime('%d/%m')
            if len(d) == 10 and d[4] == '-' and d[7] == '-' else d
            for d in sorted(cont_dias.keys())
        ],
        'grafico_dias': [cont_dias[d] for d in sorted(cont_dias.keys())]
    }

@app.route('/dash_turno')
def dash_turno():
    if not session.get('logado'):
        return redirect(url_for('login'))
    if not (is_admin_session() or tem_acesso_modulo('externos')):
        flash('Seu perfil não tem permissão para acessar o Dashboard por Turno.', 'danger')
        return redirect(url_for('admin'))

    conn = get_db_connection()
    rows = conn.execute('''
        SELECT data_execucao, hora_inicial, hora_final, turno, os_integrada, veiculo, descricao_veiculo,
               oficina, descricao_oficina, mecanico, nome_mecanico, servico, descricao_servico,
               compartimento, descricao_compartimento, quantidade
        FROM chb_turno_registros
        ORDER BY data_execucao DESC, hora_inicial DESC
    ''').fetchall()
    conn.close()

    registros = [dict(r) for r in rows]
    stats = gerar_stats_turno_chb(registros)
    return render_template(
        'dash_turno.html',
        stats=stats,
        registros=registros,
        tem_dados=(len(registros) > 0),
        data_geracao=datetime.now().strftime('%d/%m/%Y %H:%M:%S')
    )

@app.route('/api/upload_chb_turno', methods=['POST'])
def upload_chb_turno():
    if not session.get('logado'):
        return {"status": "erro"}, 403
    if not (is_admin_session() or tem_acesso_modulo('externos')):
        flash('Sem permissão para importar relatório de turnos.', 'danger')
        return redirect(url_for('admin'))
    if 'file' not in request.files:
        flash('Nenhum ficheiro selecionado.', 'danger')
        return redirect(url_for('dash_turno'))

    file = request.files['file']
    filename = (file.filename or '').lower()
    if filename == '':
        flash('Nenhum ficheiro selecionado.', 'danger')
        return redirect(url_for('dash_turno'))

    registros = []
    try:
        if filename.endswith('.xlsx'):
            try:
                import pandas as pd
            except ImportError:
                flash('Para ler Excel instale "pandas".', 'warning')
                return redirect(url_for('dash_turno'))

            xls = pd.ExcelFile(file)
            df = pd.read_excel(xls, sheet_name=xls.sheet_names[0])
            for _, row in df.iterrows():
                row_norm = {normalizar_chave_chb(k): ('' if pd.isna(v) else str(v).strip()) for k, v in row.items()}
                hora_ini = parse_hora_chb(obter_valor_chb(row_norm, ['HORA INICIAL']))
                hora_fim = parse_hora_chb(obter_valor_chb(row_norm, ['HORA FINAL']))
                turno = turno_por_hora(hora_ini or hora_fim)
                data_exec = parse_data_execucao_chb(obter_valor_chb(row_norm, ['DATA DE EXECUCAO', 'DATA EXECUCAO']))
                serv_desc = obter_valor_chb(row_norm, ['DESCRICAO SERVICO'])
                nome_mec = obter_valor_chb(row_norm, ['NOME MECANICO'])
                if not data_exec and not hora_ini and not serv_desc and not nome_mec:
                    continue
                qtd_txt = obter_valor_chb(row_norm, ['QUANTIDADE']).replace(',', '.')
                try:
                    qtd = float(qtd_txt) if qtd_txt else 0.0
                except:
                    qtd = 0.0
                registros.append((
                    data_exec,
                    hora_ini,
                    hora_fim,
                    turno,
                    obter_valor_chb(row_norm, ['OS - INTEGRADA', 'OS INTEGRADA']),
                    obter_valor_chb(row_norm, ['VEICULO', 'FROTA']),
                    obter_valor_chb(row_norm, ['DESCRICAO VEICULO']),
                    obter_valor_chb(row_norm, ['OFICINA']),
                    obter_valor_chb(row_norm, ['DESCRICAO OFICINA']),
                    obter_valor_chb(row_norm, ['MECANICO']),
                    nome_mec,
                    obter_valor_chb(row_norm, ['SERVICO']),
                    serv_desc,
                    obter_valor_chb(row_norm, ['COMPARTIMENTO']),
                    obter_valor_chb(row_norm, ['DESCRICAO COMPART.']),
                    qtd
                ))
        else:
            content_bytes = file.read()
            try:
                content_str = content_bytes.decode('utf-8-sig')
            except:
                content_str = content_bytes.decode('latin1', errors='ignore')
            lines = content_str.splitlines()
            delimiter = ';' if lines and ';' in lines[0] else ','
            reader = csv.DictReader(lines, delimiter=delimiter)
            for row in reader:
                row_norm = {normalizar_chave_chb(k): (str(v).strip() if v is not None else '') for k, v in row.items() if k}
                hora_ini = parse_hora_chb(obter_valor_chb(row_norm, ['HORA INICIAL']))
                hora_fim = parse_hora_chb(obter_valor_chb(row_norm, ['HORA FINAL']))
                turno = turno_por_hora(hora_ini or hora_fim)
                data_exec = parse_data_execucao_chb(obter_valor_chb(row_norm, ['DATA DE EXECUCAO', 'DATA EXECUCAO']))
                serv_desc = obter_valor_chb(row_norm, ['DESCRICAO SERVICO'])
                nome_mec = obter_valor_chb(row_norm, ['NOME MECANICO'])
                if not data_exec and not hora_ini and not serv_desc and not nome_mec:
                    continue
                qtd_txt = obter_valor_chb(row_norm, ['QUANTIDADE']).replace(',', '.')
                try:
                    qtd = float(qtd_txt) if qtd_txt else 0.0
                except:
                    qtd = 0.0
                registros.append((
                    data_exec,
                    hora_ini,
                    hora_fim,
                    turno,
                    obter_valor_chb(row_norm, ['OS - INTEGRADA', 'OS INTEGRADA']),
                    obter_valor_chb(row_norm, ['VEICULO', 'FROTA']),
                    obter_valor_chb(row_norm, ['DESCRICAO VEICULO']),
                    obter_valor_chb(row_norm, ['OFICINA']),
                    obter_valor_chb(row_norm, ['DESCRICAO OFICINA']),
                    obter_valor_chb(row_norm, ['MECANICO']),
                    nome_mec,
                    obter_valor_chb(row_norm, ['SERVICO']),
                    serv_desc,
                    obter_valor_chb(row_norm, ['COMPARTIMENTO']),
                    obter_valor_chb(row_norm, ['DESCRICAO COMPART.']),
                    qtd
                ))

        conn = get_db_connection()
        conn.execute('DELETE FROM chb_turno_registros')
        conn.executemany('''
            INSERT INTO chb_turno_registros (
                data_execucao, hora_inicial, hora_final, turno, os_integrada, veiculo, descricao_veiculo,
                oficina, descricao_oficina, mecanico, nome_mecanico, servico, descricao_servico,
                compartimento, descricao_compartimento, quantidade
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', registros)
        conn.commit()
        conn.close()

        flash(f'Relatório CHB de turnos importado com sucesso! {len(registros)} linhas processadas.', 'success')
    except Exception as e:
        flash(f'Erro ao processar relatório CHB de turnos: {str(e)}', 'danger')

    return redirect(url_for('dash_turno'))


def categorizar_especialidade_os(centro_custo_nome, descricao_veiculo):
    cc = str(centro_custo_nome or '').upper()
    desc = str(descricao_veiculo or '').upper()
    if 'REBOQUE' in cc:
        return 'REBOQUE'
    if 'CAMINHAO' in cc:
        return 'CAMINHÃO'
    if 'TRATOR' in cc:
        return 'TRATOR'
    if 'COLHEDORA' in cc:
        return 'COLHEDORA'
    if 'MAQUINAS PESADAS' in cc or 'MAQUINA PESADA' in cc:
        return 'MÁQUINA PESADA'
    if 'IRRIGACAO' in cc:
        return 'EQUIP. IRRIGAÇÃO'
    if 'VEICULOS LEVES' in cc:
        if 'MOTOCICLETA' in desc:
            return 'MOTOCICLETA'
        return 'VEÍCULO LEVE'
    if 'IMPLEMENTO' in cc:
        if 'AREA VIVENCIA' in desc or 'VIVENCIA' in desc:
            return 'ÁREA VIVÊNCIA'
        return 'IMPLEMENTO'
    if 'GERACAO DE ENERGIA' in cc or 'ENERGIA INDUSTRIA' in cc:
        return 'EQUIP. INDUSTRIAL'
    if 'BORRACHARIA' in cc:
        return 'CAMINHÃO'
    if 'MOTOCICLETA' in desc:
        return 'MOTOCICLETA'
    if 'AREA VIVENCIA' in desc or 'VIVENCIA' in desc:
        return 'ÁREA VIVÊNCIA'
    if 'CAMINHAO' in desc or 'VOLVO' in desc:
        return 'CAMINHÃO'
    if 'TRATOR' in desc:
        return 'TRATOR'
    if 'REBOQUE' in desc or 'SEMI REBOQUE' in desc or 'CARRETA' in desc:
        return 'REBOQUE'
    if 'COLHEDORA' in desc:
        return 'COLHEDORA'
    return 'OUTROS'


@app.route('/dash_os')
def dash_os():
    if not session.get('logado'):
        return redirect(url_for('login'))
    if not (is_admin_session() or tem_acesso_modulo('externos')):
        flash('Seu perfil não tem permissão para acessar o Dashboard de OS.', 'danger')
        return redirect(url_for('admin'))

    conn = get_db_connection()
    conn.execute('''CREATE TABLE IF NOT EXISTS os_registros (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nro_os TEXT, status TEXT, data_abertura TEXT, veiculo TEXT, placa TEXT,
        descricao_veiculo TEXT, manutencao TEXT, centro_custo_nome TEXT,
        oficina_nome TEXT, tipo_os TEXT, solicitante_nome TEXT, data_liberacao TEXT,
        fundo_agricola TEXT, custo_oficina REAL DEFAULT 0, custo_mao_obra REAL DEFAULT 0,
        pecas_consumidas REAL DEFAULT 0, valor_os REAL DEFAULT 0,
        descricao_problema TEXT DEFAULT '', especialidade TEXT DEFAULT '',
        dias_aberta INTEGER DEFAULT 0
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS frotas_disp (
        id_frota TEXT PRIMARY KEY,
        descricao TEXT DEFAULT '',
        especialidade TEXT DEFAULT '',
        agrupamento TEXT DEFAULT '',
        proprio TEXT DEFAULT 'SIM',
        cod_frota TEXT DEFAULT ''
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS frotas_terceiros (
        id_frota TEXT PRIMARY KEY,
        observacao TEXT DEFAULT ''
    )''')
    rows = conn.execute('''
        SELECT nro_os, status, data_abertura, veiculo, placa, descricao_veiculo,
               manutencao, centro_custo_nome, oficina_nome, tipo_os,
               solicitante_nome, data_liberacao, fundo_agricola,
               custo_oficina, custo_mao_obra, pecas_consumidas, valor_os,
               descricao_problema, especialidade, dias_aberta
        FROM os_registros
        ORDER BY data_abertura DESC
    ''').fetchall()
    frotas_rows = conn.execute(
        'SELECT id_frota, descricao, especialidade, agrupamento, proprio, cod_frota FROM frotas_disp ORDER BY id_frota'
    ).fetchall()
    terceiros_rows = conn.execute(
        'SELECT id_frota, observacao FROM frotas_terceiros ORDER BY id_frota'
    ).fetchall()
    conn.close()

    registros    = [dict(r) for r in rows]
    frotas_data  = [dict(r) for r in frotas_rows]
    terceiros    = [dict(r) for r in terceiros_rows]

    return render_template(
        'dash_os.html',
        registros=registros,
        frotas_data=frotas_data,
        terceiros=terceiros,
        tem_dados=len(registros) > 0,
        data_geracao=datetime.now().strftime('%d/%m/%Y %H:%M:%S')
    )


@app.route('/api/upload_os', methods=['POST'])
def upload_os():
    if not session.get('logado'):
        return jsonify({'status': 'erro'}), 403
    if not (is_admin_session() or tem_acesso_modulo('externos')):
        flash('Sem permissão para importar OS.', 'danger')
        return redirect(url_for('dash_os'))

    if 'file' not in request.files:
        flash('Nenhum arquivo selecionado.', 'danger')
        return redirect(url_for('dash_os'))

    file = request.files['file']
    if not file.filename:
        flash('Nenhum arquivo selecionado.', 'danger')
        return redirect(url_for('dash_os'))

    try:
        import pandas as pd
        import unicodedata
        from io import BytesIO

        content = file.read()
        buf = BytesIO(content)

        df_raw = pd.read_excel(buf, header=None, nrows=30, dtype=str)
        header_row = 17
        for idx, row in df_raw.iterrows():
            val = str(row.iloc[0] if not pd.isna(row.iloc[0]) else '').strip()
            if val in ('Nro OS', 'NRO OS', 'Nro. OS'):
                header_row = idx
                break

        buf.seek(0)
        df = pd.read_excel(buf, skiprows=header_row, dtype=str)

        def _norm(s):
            txt = str(s or '').strip().upper()
            txt = unicodedata.normalize('NFKD', txt)
            return ' '.join(''.join(c for c in txt if not unicodedata.combining(c)).split())

        col_map = {_norm(c): c for c in df.columns}

        def _gc(*chaves):
            for k in chaves:
                nk = _norm(k)
                if nk in col_map:
                    return col_map[nk]
            return None

        c_nro_os      = _gc('Nro OS', 'NRO OS')
        c_status      = _gc('Status')
        c_data        = _gc('Data')
        c_veiculo     = _gc('Veículo', 'Veiculo')
        c_placa       = _gc('Placa')
        c_desc_v      = _gc('Descrição Veículo', 'Descricao Veiculo')
        c_manutencao  = _gc('Manutenção', 'Manutencao')
        c_cc_nome     = _gc('Nome do Centro de Custo', 'Nome Centro de Custo')
        c_oficina     = _gc('Nome Oficina Prevista')
        c_tipo_os     = _gc('Descrição', 'Descricao')
        c_solicitante = _gc('Nome do Solicitante', 'Nome Solicitante')
        c_data_lib    = _gc('Data Liberação', 'Data Liberacao')
        c_fundo       = _gc('Fundo Agrícola', 'Fundo Agricola')
        c_custo_of    = _gc('Total do Custo Oficina', 'Total Custo Oficina')
        c_custo_mo    = _gc('Total Custo Mão de Obra', 'Total Custo Mao de Obra')
        c_pecas       = _gc('Peças Consumidas', 'Pecas Consumidas')
        c_valor_os    = _gc('Valor da OS')
        c_desc_prob   = _gc('Descrição do Problema', 'Descricao do Problema')

        def _sv(row, col):
            if col is None or col not in row.index:
                return ''
            v = row[col]
            if v is None or (isinstance(v, float) and pd.isna(v)):
                return ''
            return str(v).strip()

        def _fv(row, col):
            txt = _sv(row, col).replace(',', '.')
            if not txt or txt in ('0', '0.0', 'nan', 'none', '-'):
                return 0.0
            try:
                return float(txt)
            except Exception:
                return 0.0

        def _parse_dt(val):
            txt = str(val or '').strip()
            if not txt or txt in ('0', 'nan', 'None', '-'):
                return None
            if hasattr(val, 'strftime'):
                return val.strftime('%Y-%m-%d')
            for fmt in ['%d/%m/%y', '%d/%m/%Y', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d']:
                try:
                    return datetime.strptime(txt[:10], fmt).strftime('%Y-%m-%d')
                except Exception:
                    pass
            return None

        hoje = datetime.now().date()
        registros_db = []

        for _, row in df.iterrows():
            nro_os = _sv(row, c_nro_os)
            if not nro_os or nro_os in ('nan', 'None', ''):
                continue
            try:
                int(float(nro_os))
            except Exception:
                continue

            data_abertura = _parse_dt(_sv(row, c_data))
            data_lib = _parse_dt(_sv(row, c_data_lib))
            desc_veiculo = _sv(row, c_desc_v)
            centro_custo = _sv(row, c_cc_nome)

            dias_aberta = 0
            if data_abertura:
                try:
                    dt_ab = datetime.strptime(data_abertura, '%Y-%m-%d').date()
                    if data_lib:
                        dt_lb = datetime.strptime(data_lib, '%Y-%m-%d').date()
                        dias_aberta = max(0, (dt_lb - dt_ab).days)
                    else:
                        dias_aberta = max(0, (hoje - dt_ab).days)
                except Exception:
                    pass

            especialidade = categorizar_especialidade_os(centro_custo, desc_veiculo)

            registros_db.append((
                nro_os,
                _sv(row, c_status),
                data_abertura or '',
                _sv(row, c_veiculo),
                _sv(row, c_placa),
                desc_veiculo,
                _sv(row, c_manutencao),
                centro_custo,
                _sv(row, c_oficina),
                _sv(row, c_tipo_os),
                _sv(row, c_solicitante),
                data_lib or '',
                _sv(row, c_fundo),
                _fv(row, c_custo_of),
                _fv(row, c_custo_mo),
                _fv(row, c_pecas),
                _fv(row, c_valor_os),
                _sv(row, c_desc_prob),
                especialidade,
                dias_aberta
            ))

        conn = get_db_connection()
        conn.execute('DELETE FROM os_registros')
        conn.executemany('''
            INSERT INTO os_registros (
                nro_os, status, data_abertura, veiculo, placa, descricao_veiculo,
                manutencao, centro_custo_nome, oficina_nome, tipo_os,
                solicitante_nome, data_liberacao, fundo_agricola,
                custo_oficina, custo_mao_obra, pecas_consumidas, valor_os,
                descricao_problema, especialidade, dias_aberta
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ''', registros_db)
        conn.commit()
        conn.close()

        flash(f'Relatório de OS importado com sucesso! {len(registros_db)} ordens processadas.', 'success')
    except ImportError:
        flash('Instale "pandas" e "openpyxl" para importar Excel: pip install pandas openpyxl', 'warning')
    except Exception as e:
        flash(f'Erro ao processar o arquivo: {str(e)}', 'danger')

    return redirect(url_for('dash_os'))


@app.route('/api/upload_os_preventiva', methods=['POST'])
def upload_os_preventiva():
    """Importa a base de O.S. Mensal ou O.S. Abertas (?origem=mensal|abertas)
    usada pelo classificador automático de Preventiva/Pit Stop da Programação
    Semanal. Tabela própria (programacao_os_import) - não mexe em os_registros,
    que pertence a Serviços Externos. Upsert por nro_os: as duas origens
    coexistem e se atualizam sem se apagar, igual ao BASE_OS + OS_ABERTAS do
    VBA original."""
    if not session.get('logado'):
        return jsonify({'status': 'erro'}), 403
    if not tem_acesso_modulo('programacao'):
        flash('Sem permissão para importar O.S. na Programação.', 'danger')
        return redirect(url_for('programacao'))

    origem = (request.args.get('origem') or '').strip().upper()
    if origem not in ('MENSAL', 'ABERTAS'):
        flash('Origem de importação inválida.', 'danger')
        return redirect(url_for('programacao'))

    if 'file' not in request.files or not request.files['file'].filename:
        flash('Nenhum arquivo selecionado.', 'danger')
        return redirect(url_for('programacao'))

    file = request.files['file']

    try:
        import pandas as pd
        from io import BytesIO

        content = file.read()
        buf = BytesIO(content)

        df_raw = pd.read_excel(buf, header=None, nrows=30, dtype=str)
        header_row = 0
        for idx, row in df_raw.iterrows():
            val = str(row.iloc[0] if not pd.isna(row.iloc[0]) else '').strip()
            if val in ('Nro OS', 'NRO OS', 'Nro. OS'):
                header_row = idx
                break

        buf.seek(0)
        df = pd.read_excel(buf, skiprows=header_row, dtype=str)

        col_map = {normalizar_chave_chb(c): c for c in df.columns}

        def _gc(*chaves):
            for k in chaves:
                nk = normalizar_chave_chb(k)
                if nk in col_map:
                    return col_map[nk]
            return None

        c_nro_os     = _gc('Nro OS', 'NRO OS')
        c_status     = _gc('Status')
        c_data       = _gc('Data')
        c_veiculo    = _gc('Veículo', 'Veiculo')
        c_manutencao = _gc('Manutenção', 'Manutencao')
        c_desc_prob  = _gc('Descrição do Problema', 'Descricao do Problema')
        c_desc       = _gc('Descrição', 'Descricao')
        c_data_lib   = _gc('Data Liberação', 'Data Liberacao')

        faltando = [nome for nome, col in [('Nro OS', c_nro_os), ('Veículo', c_veiculo)] if col is None]
        if faltando:
            flash(f'Não encontrei a(s) coluna(s) obrigatória(s): {", ".join(faltando)}. Verifique o cabeçalho do arquivo.', 'danger')
            return redirect(url_for('programacao'))

        def _sv(row, col):
            if col is None or col not in row.index:
                return ''
            v = row[col]
            if v is None or (isinstance(v, float) and pd.isna(v)):
                return ''
            return str(v).strip()

        def _parse_dt(val):
            txt = str(val or '').strip()
            if not txt or txt in ('0', 'nan', 'None', '-'):
                return None
            if hasattr(val, 'strftime'):
                return val.strftime('%Y-%m-%d')
            for fmt in ['%d/%m/%y', '%d/%m/%Y', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d']:
                try:
                    return datetime.strptime(txt[:10], fmt).strftime('%Y-%m-%d')
                except Exception:
                    pass
            return None

        conn = get_db_connection()
        p = ler_parametros(conn)
        termos_prev = [_texto_os(t) for t in (p.get('termos_preventiva') or '').split(',') if t.strip()]
        termos_pit = [_texto_os(t) for t in (p.get('termos_pitstop') or '').split(',') if t.strip()]

        agora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        registros = []
        ignoradas = 0

        for _, row in df.iterrows():
            nro_os = _sv(row, c_nro_os)
            if not nro_os or nro_os in ('nan', 'None', ''):
                continue
            try:
                int(float(nro_os))
            except Exception:
                continue

            veiculo_raw = _sv(row, c_veiculo)
            if not veiculo_raw:
                ignoradas += 1
                continue

            descricao = _sv(row, c_desc_prob) or _sv(row, c_desc)
            manutencao_raw = _sv(row, c_manutencao)
            tipo_classificado = _classificar_tipo_os(descricao, manutencao_raw, termos_prev, termos_pit)

            registros.append((
                nro_os,
                primeira_frota_do_texto(veiculo_raw),
                veiculo_raw,
                _sv(row, c_status),
                tipo_classificado,
                manutencao_raw,
                descricao,
                _parse_dt(_sv(row, c_data)) or '',
                _parse_dt(_sv(row, c_data_lib)) or '',
                origem,
                agora,
            ))

        conn.executemany('''
            INSERT INTO programacao_os_import (
                nro_os, id_frota, veiculo_raw, status, tipo_classificado,
                manutencao_raw, descricao, data_abertura, data_liberacao,
                origem_planilha, atualizado_em
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(nro_os) DO UPDATE SET
                id_frota=excluded.id_frota, veiculo_raw=excluded.veiculo_raw,
                status=excluded.status, tipo_classificado=excluded.tipo_classificado,
                manutencao_raw=excluded.manutencao_raw, descricao=excluded.descricao,
                data_abertura=excluded.data_abertura, data_liberacao=excluded.data_liberacao,
                origem_planilha=excluded.origem_planilha, atualizado_em=excluded.atualizado_em
        ''', registros)
        conn.commit()
        conn.close()

        msg = f'{"O.S. Mensal" if origem == "MENSAL" else "O.S. Abertas"} importada: {len(registros)} ordens processadas.'
        if ignoradas:
            msg += f' {ignoradas} linha(s) ignorada(s) por não ter frota identificada.'
        flash(msg, 'success')
    except ImportError:
        flash('Instale "pandas" e "openpyxl" para importar Excel: pip install pandas openpyxl', 'warning')
    except Exception as e:
        flash(f'Erro ao processar o arquivo: {str(e)}', 'danger')

    return redirect(url_for('programacao'))


@app.route('/api/upload_frotas', methods=['POST'])
def upload_frotas():
    if not session.get('logado'):
        return jsonify({'status': 'erro'}), 403

    if 'file' not in request.files:
        flash('Nenhum arquivo selecionado.', 'danger')
        return redirect(url_for('dash_os'))

    file = request.files['file']
    if not file.filename:
        flash('Nenhum arquivo selecionado.', 'danger')
        return redirect(url_for('dash_os'))

    try:
        import pandas as pd
        from io import BytesIO

        content = file.read()
        filename = file.filename.lower()

        if filename.endswith('.csv'):
            df = pd.read_csv(BytesIO(content), sep=';', dtype=str, encoding='latin-1', on_bad_lines='skip')
        else:
            df = pd.read_excel(BytesIO(content), dtype=str)

        df.columns = [str(c).strip() for c in df.columns]
        _cols_lower = {c.lower(): c for c in df.columns}

        def _col(*names):
            for n in names:
                found = _cols_lower.get(n.lower())
                if found is not None:
                    return found
            return None

        c_id    = _col('id_frota')
        c_cod   = _col('CodFrota', 'cod_frota', 'codfrota')
        c_desc  = _col('descricao_frota', 'descricao')
        c_micro = _col('descricao_especialidade', 'especialidade')
        c_agrup = _col('descricao_especialidadeAgrup', 'agrupamento')
        c_prop  = _col('proprio')

        if not c_id:
            flash('Coluna "id_frota" não encontrada no arquivo.', 'danger')
            return redirect(url_for('dash_os'))

        def _norm_id(val):
            s = str(val or '').strip()
            if s.endswith('.0'):
                try: s = str(int(float(s)))
                except Exception: pass
            return s

        registros = []
        for _, row in df.iterrows():
            id_frota = _norm_id(row.get(c_id, ''))
            if not id_frota or id_frota in ('nan', 'None', ''):
                continue
            cod_frota     = _norm_id(row.get(c_cod, '')) if c_cod else ''
            descricao     = str(row.get(c_desc,  '') or '').strip() if c_desc  else ''
            especialidade = str(row.get(c_micro, '') or '').strip() if c_micro else ''
            agrupamento   = str(row.get(c_agrup, '') or '').strip() if c_agrup else ''
            proprio_raw   = str(row.get(c_prop, 'SIM') or 'SIM').strip().upper() if c_prop else 'SIM'
            proprio       = 'SIM' if proprio_raw == 'SIM' else 'NAO'
            registros.append((id_frota, descricao, especialidade, agrupamento, proprio, cod_frota))

        conn = get_db_connection()
        conn.execute('DELETE FROM frotas_disp')
        conn.executemany(
            'INSERT INTO frotas_disp (id_frota, descricao, especialidade, agrupamento, proprio, cod_frota) VALUES (?,?,?,?,?,?)',
            registros
        )
        conn.commit()
        conn.close()

        flash(f'Base de frotas importada com sucesso! {len(registros)} veículos processados.', 'success')
    except ImportError:
        flash('Instale "pandas" e "openpyxl": pip install pandas openpyxl', 'warning')
    except Exception as e:
        flash(f'Erro ao processar a base de frotas: {str(e)}', 'danger')

    return redirect(url_for('dash_os'))


@app.route('/api/frotas_terceiros', methods=['GET'])
def listar_frotas_terceiros():
    if not session.get('logado'):
        return jsonify({'status': 'erro'}), 403
    conn = get_db_connection()
    rows = conn.execute('SELECT id_frota, observacao FROM frotas_terceiros ORDER BY id_frota').fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/api/frotas_terceiros/salvar', methods=['POST'])
def salvar_frotas_terceiros():
    if not session.get('logado'):
        return jsonify({'status': 'erro'}), 403
    data = request.get_json(force=True)
    ids  = data.get('ids', [])
    obs  = data.get('observacao', '').strip()
    conn = get_db_connection()
    saved = 0
    for fid in ids:
        fid = str(fid).strip().upper()
        if fid:
            conn.execute(
                'INSERT OR IGNORE INTO frotas_terceiros (id_frota, observacao) VALUES (?, ?)',
                (fid, obs)
            )
            saved += 1
    conn.commit()
    conn.close()
    return jsonify({'status': 'ok', 'saved': saved})


@app.route('/api/frotas_terceiros/excluir', methods=['POST'])
def excluir_frotas_terceiros():
    if not session.get('logado'):
        return jsonify({'status': 'erro'}), 403
    data = request.get_json(force=True)
    ids  = data.get('ids', [])
    conn = get_db_connection()
    for fid in ids:
        conn.execute('DELETE FROM frotas_terceiros WHERE id_frota = ?', (str(fid).strip().upper(),))
    conn.commit()
    conn.close()
    return jsonify({'status': 'ok', 'removed': len(ids)})


@app.route('/cadastrar_frota', methods=['POST'])
def cadastrar_frota():
    if not session.get('logado'): return redirect(url_for('login'))

    frota = request.form.get('frota').strip().upper()
    descricao = request.form.get('descricao', '').strip().upper() or 'SEM DESCRIÇÃO'
    agrupamento = request.form.get('agrupamento', '').strip().upper() or 'GERAL'
    especialidade = request.form.get('especialidade', '').strip().upper() or 'OUTROS'
    setor = request.form.get('setor', '').strip().upper()

    if not frota:
        flash('O ID / Placa da frota é obrigatório.', 'danger')
        return redirect(url_for('admin'))

    conn = get_db_connection()
    existente = conn.execute('SELECT id_frota FROM frotas WHERE id_frota = ?', (frota,)).fetchone()

    if existente:
        conn.execute('UPDATE frotas SET descricao = ?, agrupamento = ?, especialidade = ?, setor = ? WHERE id_frota = ?',
                     (descricao, agrupamento, especialidade, setor, frota))
        flash(f'Dados da frota {frota} foram atualizados com sucesso!', 'warning')
    else:
        conn.execute('INSERT INTO frotas (id_frota, descricao, setor, especialidade, agrupamento) VALUES (?, ?, ?, ?, ?)',
                     (frota, descricao, setor, especialidade, agrupamento))
        flash(f'Frota {frota} cadastrada com sucesso!', 'success')

    conn.commit()
    conn.close()
    return redirect(url_for('admin'))

@app.route('/api/perfis/salvar', methods=['POST'])
def salvar_perfil_acesso():
    if not session.get('logado'): return redirect(url_for('login'))
    if not is_admin_session():
        flash('Apenas o administrador pode gerir perfis.', 'danger')
        return redirecionar_primeiro_modulo_permitido()

    perfil_id = request.form.get('perfil_id', '').strip()
    nome = request.form.get('nome_perfil', '').strip().upper()
    senha_perfil = request.form.get('senha_perfil', '').strip()
    if not nome:
        flash('Informe o nome do perfil.', 'danger')
        return redirect(url_for('admin'))

    acesso_bloqueios = 1 if request.form.get('acesso_bloqueios') else 0
    acesso_programacao = 1 if request.form.get('acesso_programacao') else 0
    acesso_lavagem = 1 if request.form.get('acesso_lavagem') else 0
    acesso_analises = 1 if request.form.get('acesso_analises') else 0
    acesso_abastecimentos = 1 if request.form.get('acesso_abastecimentos') else 0
    acesso_externos = 1 if request.form.get('acesso_externos') else 0
    acesso_ferramentaria = 1 if request.form.get('acesso_ferramentaria') else 0
    acesso_compras = 1 if request.form.get('acesso_compras') else 0
    compras_criar = 1
    compras_editar = 1
    compras_alterar_status = 1

    conn = get_db_connection()
    existente = None
    if perfil_id and perfil_id.isdigit():
        existente = conn.execute('SELECT id, IFNULL(senha_hash, "") as senha_hash FROM perfis_acesso WHERE id = ?', (perfil_id,)).fetchone()
    elif nome:
        existente = conn.execute('SELECT id, IFNULL(senha_hash, "") as senha_hash FROM perfis_acesso WHERE nome = ?', (nome,)).fetchone()

    try:
        if existente:
            if senha_perfil:
                conn.execute('''
                    UPDATE perfis_acesso
                    SET nome = ?, acesso_bloqueios = ?, acesso_programacao = ?, acesso_lavagem = ?, acesso_analises = ?, acesso_abastecimentos = ?, acesso_externos = ?, acesso_ferramentaria = ?,
                        acesso_compras = ?, compras_criar = ?, compras_editar = ?, compras_alterar_status = ?, senha_hash = ?, senha_txt = ?
                    WHERE id = ?
                ''', (nome, acesso_bloqueios, acesso_programacao, acesso_lavagem, acesso_analises, acesso_abastecimentos, acesso_externos, acesso_ferramentaria,
                      acesso_compras, compras_criar, compras_editar, compras_alterar_status, generate_password_hash(senha_perfil), senha_perfil, existente['id']))
            else:
                if not existente['senha_hash']:
                    conn.close()
                    flash('Este perfil ainda não tem senha. Informe uma senha para concluir.', 'danger')
                    return redirect(url_for('admin'))
                conn.execute('''
                    UPDATE perfis_acesso
                    SET nome = ?, acesso_bloqueios = ?, acesso_programacao = ?, acesso_lavagem = ?, acesso_analises = ?, acesso_abastecimentos = ?, acesso_externos = ?, acesso_ferramentaria = ?,
                        acesso_compras = ?, compras_criar = ?, compras_editar = ?, compras_alterar_status = ?
                    WHERE id = ?
                ''', (nome, acesso_bloqueios, acesso_programacao, acesso_lavagem, acesso_analises, acesso_abastecimentos, acesso_externos, acesso_ferramentaria,
                      acesso_compras, compras_criar, compras_editar, compras_alterar_status, existente['id']))
            flash(f'Perfil {nome} atualizado com sucesso!', 'warning')
        else:
            if not senha_perfil:
                conn.close()
                flash('Informe uma senha para criar o perfil.', 'danger')
                return redirect(url_for('admin'))
            conn.execute('''
                INSERT INTO perfis_acesso (nome, acesso_bloqueios, acesso_programacao, acesso_lavagem, acesso_analises, acesso_abastecimentos, acesso_externos, acesso_ferramentaria,
                    acesso_compras, compras_criar, compras_editar, compras_alterar_status, senha_hash, senha_txt)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (nome, acesso_bloqueios, acesso_programacao, acesso_lavagem, acesso_analises, acesso_abastecimentos, acesso_externos, acesso_ferramentaria,
                  acesso_compras, compras_criar, compras_editar, compras_alterar_status, generate_password_hash(senha_perfil), senha_perfil))
            flash(f'Perfil {nome} criado com sucesso!', 'success')

        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        flash('Já existe um perfil com esse nome.', 'danger')
    finally:
        conn.close()
    return redirect(url_for('admin'))

@app.route('/api/perfis/excluir/<int:id_perfil>', methods=['POST'])
def excluir_perfil_acesso(id_perfil):
    if not session.get('logado'): return redirect(url_for('login'))
    if not is_admin_session():
        flash('Apenas o administrador pode gerir perfis.', 'danger')
        return redirecionar_primeiro_modulo_permitido()
    conn = get_db_connection()
    perfil = conn.execute('SELECT nome FROM perfis_acesso WHERE id = ?', (id_perfil,)).fetchone()
    if perfil:
        conn.execute('DELETE FROM perfis_acesso WHERE id = ?', (id_perfil,))
        flash(f"Perfil {perfil['nome']} removido com sucesso!", 'danger')
    else:
        flash('Perfil não encontrado.', 'warning')
    conn.commit()
    conn.close()
    return redirect(url_for('admin'))

@app.route('/api/cadastrar_setor_frotas', methods=['POST'])
def cadastrar_setor_frotas():
    if not session.get('logado'): return jsonify({'status': 'erro', 'mensagem': 'Não autenticado'}), 401
    data = request.get_json(silent=True) or {}
    setor = (data.get('setor') or data.get('agrupamento') or '').strip().upper()
    frotas = data.get('frotas', [])
    if not setor or not frotas:
        return jsonify({'status': 'erro', 'mensagem': 'Setor e frotas são obrigatórios.'}), 400

    conn = get_db_connection()
    atualizadas = 0
    cadastradas = 0
    for frota in frotas:
        frota = str(frota).strip().upper()
        if not frota: continue
        existente = conn.execute('SELECT id_frota FROM frotas WHERE id_frota = ?', (frota,)).fetchone()
        if existente:
            conn.execute('UPDATE frotas SET setor = ? WHERE id_frota = ?', (setor, frota))
            atualizadas += 1
        else:
            conn.execute('INSERT INTO frotas (id_frota, descricao, setor, especialidade, agrupamento) VALUES (?, ?, ?, ?, ?)',
                         (frota, 'SEM DESCRIÇÃO', setor, 'OUTROS', 'GERAL'))
            cadastradas += 1
    conn.commit()
    conn.close()
    return jsonify({'status': 'sucesso', 'atualizadas': atualizadas, 'cadastradas': cadastradas})

@app.route('/api/excluir_setor_frotas', methods=['POST'])
def excluir_setor_frotas():
    if not session.get('logado'): return jsonify({'status': 'erro', 'mensagem': 'Não autenticado'}), 401
    data = request.get_json(silent=True) or {}
    setor = (data.get('setor') or '').strip().upper()
    if not setor:
        return jsonify({'status': 'erro', 'mensagem': 'Setor é obrigatório.'}), 400

    conn = get_db_connection()
    vinculadas = conn.execute("SELECT COUNT(*) as total FROM frotas WHERE UPPER(TRIM(COALESCE(setor, ''))) = ?", (setor,)).fetchone()
    total = vinculadas['total'] if vinculadas else 0
    if total == 0:
        conn.close()
        return jsonify({'status': 'erro', 'mensagem': 'Nenhuma frota encontrada para este setor.'}), 404

    conn.execute("UPDATE frotas SET setor = '' WHERE UPPER(TRIM(COALESCE(setor, ''))) = ?", (setor,))
    conn.commit()
    conn.close()
    return jsonify({'status': 'sucesso', 'removidas': total})

@app.route('/api/cadastrar_frente_frotas', methods=['POST'])
def cadastrar_frente_frotas():
    """Espelha /api/cadastrar_setor_frotas, mas grava em frentes_config em vez
    de frotas.setor - mesma UX de colar uma lista de frotas, agora pra Frente,
    dentro do mesmo botão 'Estrutura' da Programação Semanal."""
    if not session.get('logado'): return jsonify({'status': 'erro', 'mensagem': 'Não autenticado'}), 401
    data = request.get_json(silent=True) or {}
    frente = (data.get('frente') or '').strip().upper()
    frotas = data.get('frotas', [])
    if not frente or not frotas:
        return jsonify({'status': 'erro', 'mensagem': 'Frente e frotas são obrigatórios.'}), 400

    conn = get_db_connection()
    atualizadas = 0
    for frota in frotas:
        frota = str(frota).strip().upper()
        if not frota: continue
        conn.execute('INSERT OR REPLACE INTO frentes_config (cod_frota, frente) VALUES (?, ?)', (frota, frente))
        atualizadas += 1
    conn.commit()
    conn.close()
    return jsonify({'status': 'sucesso', 'atualizadas': atualizadas})

@app.route('/api/excluir_frente_frotas', methods=['POST'])
def excluir_frente_frotas():
    if not session.get('logado'): return jsonify({'status': 'erro', 'mensagem': 'Não autenticado'}), 401
    data = request.get_json(silent=True) or {}
    frente = (data.get('frente') or '').strip().upper()
    if not frente:
        return jsonify({'status': 'erro', 'mensagem': 'Frente é obrigatória.'}), 400

    conn = get_db_connection()
    vinculadas = conn.execute("SELECT COUNT(*) as total FROM frentes_config WHERE UPPER(TRIM(frente)) = ?", (frente,)).fetchone()
    total = vinculadas['total'] if vinculadas else 0
    if total == 0:
        conn.close()
        return jsonify({'status': 'erro', 'mensagem': 'Nenhuma frota encontrada para esta frente.'}), 404

    conn.execute("DELETE FROM frentes_config WHERE UPPER(TRIM(frente)) = ?", (frente,))
    conn.commit()
    conn.close()
    return jsonify({'status': 'sucesso', 'removidas': total})


@app.route('/excluir_frota/<string:id_frota>')
def excluir_frota(id_frota):
    if not session.get('logado'): return redirect(url_for('login'))
    conn = get_db_connection()
    tem_hist = conn.execute('SELECT id FROM historico_bloqueios WHERE id_frota = ? LIMIT 1', (id_frota,)).fetchone()
    tem_prog = conn.execute('SELECT id FROM programacao_semanal WHERE id_frota = ? LIMIT 1', (id_frota,)).fetchone()

    if tem_hist or tem_prog:
        flash(f'Não é possível excluir a frota {id_frota} porque ela já possui histórico de manutenções ou programação.', 'danger')
    else:
        conn.execute('DELETE FROM frotas WHERE id_frota = ?', (id_frota,))
        flash(f'Frota {id_frota} excluída com sucesso!', 'danger')

    conn.commit()
    conn.close()
    return redirect(url_for('admin'))

@app.route('/bloquear', methods=['POST'])
def bloquear():
    if not session.get('logado'): return redirect(url_for('login'))
    if not tem_acesso_modulo('bloqueios'):
        flash('Seu perfil não tem permissão para registrar bloqueios.', 'danger')
        return redirect(url_for('admin'))
    frota = request.form.get('frota').strip()
    status = request.form.get('status')
    motivo1 = request.form.get('motivo1')
    if motivo1 == 'NOVO': motivo1 = request.form.get('motivo1_novo').strip().upper()
    motivo2 = request.form.get('motivo2')
    if motivo2 == 'NOVO': motivo2 = request.form.get('motivo2_novo').strip().upper()
    motivo_final = f"{motivo1} + {motivo2}" if motivo2 != "NENHUM" else motivo1
    data_atual = datetime.now().strftime("%d/%m/%Y")
    conn = get_db_connection()
    conn.execute('INSERT INTO historico_bloqueios (id_frota, motivo, status_oficina, data_bloqueio) VALUES (?, ?, ?, ?)', (frota, motivo_final, status, data_atual))
    conn.commit()
    conn.close()
    flash(f'Bloqueio registrado para a frota {frota}!', 'success')
    return redirect(url_for('admin'))

@app.route('/desbloquear', methods=['POST'])
def desbloquear():
    if not session.get('logado'): return redirect(url_for('login'))
    if not tem_acesso_modulo('bloqueios'):
        flash('Seu perfil não tem permissão para liberar frotas.', 'danger')
        return redirect(url_for('admin'))
    ids = request.form.get('id_bloqueio').split(',')
    observacao = request.form.get('observacao', '').strip()
    data_atual = datetime.now().strftime("%d/%m/%Y")
    conn = get_db_connection()
    for idx in ids: conn.execute('UPDATE historico_bloqueios SET data_desbloqueio = ?, observacao = ? WHERE id = ?', (data_atual, observacao, idx))
    conn.commit()
    conn.close()
    flash('Frota liberada com sucesso!', 'success')
    return redirect(url_for('admin'))

@app.route('/editar', methods=['POST'])
def editar():
    if not session.get('logado'): return redirect(url_for('login'))
    if not tem_acesso_modulo('bloqueios'):
        flash('Seu perfil não tem permissão para editar bloqueios.', 'danger')
        return redirect(url_for('admin'))
    ids = request.form.get('id_bloqueio').split(',')
    nova_data = request.form.get('nova_data').strip()
    novo_motivo = request.form.get('novo_motivo').strip().upper()
    conn = get_db_connection()
    for idx in ids: conn.execute('UPDATE historico_bloqueios SET data_bloqueio = ?, motivo = ? WHERE id = ?', (nova_data, novo_motivo, idx))
    conn.commit()
    conn.close()
    flash('Registro atualizado com sucesso!', 'warning')
    return redirect(url_for('admin'))

@app.route('/excluir_historico/<int:id_registro>')
def excluir_historico(id_registro):
    if not session.get('logado'): return redirect(url_for('login'))
    if not tem_acesso_modulo('bloqueios'):
        flash('Seu perfil não tem permissão para excluir histórico de bloqueios.', 'danger')
        return redirect(url_for('admin'))
    conn = get_db_connection()
    conn.execute('DELETE FROM historico_bloqueios WHERE id = ?', (id_registro,))
    conn.commit()
    conn.close()
    flash('Registro excluído!', 'danger')
    return redirect(url_for('admin'))

@app.route('/excluir_bloqueio_ativo/<ids>')
def excluir_bloqueio_ativo(ids):
    if not session.get('logado'): return redirect(url_for('login'))
    if not tem_acesso_modulo('bloqueios'):
        flash('Seu perfil não tem permissão para excluir bloqueios.', 'danger')
        return redirect(url_for('admin'))
    conn = get_db_connection()
    for idx in ids.split(','): conn.execute('DELETE FROM historico_bloqueios WHERE id = ?', (idx,))
    conn.commit()
    conn.close()
    flash('Bloqueio excluído da oficina!', 'danger')
    return redirect(url_for('admin'))

@app.route('/relatorio')
def relatorio():
    if not session.get('logado'): return redirect(url_for('login'))
    conn = get_db_connection()
    ativos_agrupados = get_ativos_agrupados(conn)
    conn.close()
    frotas_planas = [f for lista in ativos_agrupados.values() for f in lista]

    # PDF de bloqueios: por padrao traz todas as frotas bloqueadas, mas pode ser filtrado
    # por um unico tipo de bloqueio (ex.: so PREVENTIVA, so TROCA DE OLEO) - uma frota
    # bloqueada por varios motivos ("PREVENTIVA + PIT STOP") entra no filtro se QUALQUER
    # um dos motivos dela bater com o escolhido.
    filtro_motivo = (request.args.get('motivo') or '').strip().upper()
    if filtro_motivo and filtro_motivo != 'TODOS':
        frotas_planas = [f for f in frotas_planas if filtro_motivo in (f.get('motivo') or '').upper()]

    frotas_planas = sorted(frotas_planas, key=lambda x: x['motivo'])
    total_bloqueadas, contagem_motivos = calcular_estatisticas(ativos_agrupados)
    # Com filtro ativo, o resumo mostra a contagem JA FILTRADA (nao a da oficina toda),
    # pra bater com o que aparece na tabela abaixo.
    total_exibido = len(frotas_planas) if filtro_motivo and filtro_motivo != 'TODOS' else total_bloqueadas
    contagem_exibida = contagem_motivos if not (filtro_motivo and filtro_motivo != 'TODOS') else {}
    return render_template('relatorio.html', frotas=frotas_planas, data_hoje=datetime.now().strftime("%d/%m/%Y"), total=total_exibido, contagem=contagem_exibida, filtro_motivo=filtro_motivo)

@app.route('/programacao')
def programacao():
    if not session.get('logado'): return redirect(url_for('login'))
    if not tem_acesso_modulo('programacao'):
        flash('Seu perfil não tem permissão para acessar Programação.', 'danger')
        return redirect(url_for('admin'))
    modo_visualizacao = (request.args.get('view') or 'semana').strip().lower()
    if modo_visualizacao not in ['semana', 'mensal']:
        modo_visualizacao = 'semana'
    offset = int(request.args.get('offset', 0))
    mes_offset = int(request.args.get('mes_offset', 0))
    hoje_data = datetime.now()
    segunda_atual = hoje_data - timedelta(days=hoje_data.weekday())
    segunda_alvo = segunda_atual + timedelta(weeks=offset)
    domingo_alvo = segunda_alvo + timedelta(days=6)

    mes_base = hoje_data.replace(day=1)
    deslocamento_meses = (mes_base.month - 1) + mes_offset
    mes_alvo = mes_base.replace(
        year=mes_base.year + (deslocamento_meses // 12),
        month=(deslocamento_meses % 12) + 1
    )
    if mes_alvo.month == 12:
        proximo_mes = mes_alvo.replace(year=mes_alvo.year + 1, month=1)
    else:
        proximo_mes = mes_alvo.replace(month=mes_alvo.month + 1)
    ultimo_mes = proximo_mes - timedelta(days=1)

    if modo_visualizacao == 'mensal':
        data_inicio_str = mes_alvo.strftime('%Y-%m-%d')
        data_fim_str = ultimo_mes.strftime('%Y-%m-%d')
    else:
        data_inicio_str = segunda_alvo.strftime('%Y-%m-%d')
        data_fim_str = domingo_alvo.strftime('%Y-%m-%d')

    conn = get_db_connection()
    frotas = conn.execute('SELECT id_frota, descricao FROM frotas ORDER BY id_frota').fetchall()
    setores_rows = conn.execute('''
        SELECT setor, id_frota
        FROM frotas
        WHERE setor IS NOT NULL AND TRIM(setor) != ''
        ORDER BY setor, id_frota
    ''').fetchall()
    dias_semana = get_dias_semana(segunda_alvo)
    # Lavagem/Lubrificação deixaram de ter pagina propria (/lavagem) e agora aparecem
    # direto aqui, como mais dois tipos de servico entre os demais.
    programacao_db = get_programacao_agrupada(conn, data_inicio_str, data_fim_str, sincronizar_preventiva=(modo_visualizacao != 'mensal'))
    stats_prog = calcular_stats_prog(programacao_db)
    especialidades_na_semana = get_especialidades_da_semana(programacao_db)
    setores_na_semana = get_setores_da_semana(programacao_db)

    setores_cadastrados = []
    setor_atual = None
    for row in setores_rows:
        nome_setor = row['setor'].strip()
        if not setor_atual or setor_atual['nome'] != nome_setor:
            setor_atual = {'nome': nome_setor, 'frotas': []}
            setores_cadastrados.append(setor_atual)
        setor_atual['frotas'].append(row['id_frota'])

    # Mesma ideia de setores_cadastrados, mas pra Frente - alimenta a aba
    # "Frente" do botão Estrutura, dentro deste mesmo modal.
    frentes_rows = conn.execute('''
        SELECT frente, cod_frota FROM frentes_config
        WHERE frente IS NOT NULL AND TRIM(frente) != ''
        ORDER BY frente, cod_frota
    ''').fetchall()
    frentes_cadastradas = []
    frente_atual = None
    for row in frentes_rows:
        nome_frente = row['frente'].strip()
        if not frente_atual or frente_atual['nome'] != nome_frente:
            frente_atual = {'nome': nome_frente, 'frotas': []}
            frentes_cadastradas.append(frente_atual)
        frente_atual['frotas'].append(row['cod_frota'])

    matriz_agrupada = {}
    matriz_setor = {}
    for p in programacao_db:
        macro = p['especialidade'] if p['especialidade'] else 'GERAL'
        micro = p['especialidade_detalhe'] if p['especialidade_detalhe'] else 'OUTROS'
        if macro not in matriz_agrupada:
            matriz_agrupada[macro] = set()
        matriz_agrupada[macro].add(micro)

        setor = p.get('setor', 'SEM SETOR') or 'SEM SETOR'
        if setor not in matriz_setor:
            matriz_setor[setor] = set()
        matriz_setor[setor].add(micro)

    matriz_agrupada = {k: sorted(list(v)) for k, v in sorted(matriz_agrupada.items())}
    matriz_setor = {k: sorted(list(v)) for k, v in sorted(matriz_setor.items())}

    ultima_importacao_os = {}
    for r in conn.execute('SELECT origem_planilha, MAX(atualizado_em) as ult FROM programacao_os_import GROUP BY origem_planilha').fetchall():
        ultima_importacao_os[r['origem_planilha']] = r['ult']
    conn.close()

    calendario_mensal = get_calendario_mensal(mes_alvo)
    dias_mes = get_dias_do_mes(mes_alvo)
    nomes_meses = ['Janeiro', 'Fevereiro', 'Marco', 'Abril', 'Maio', 'Junho', 'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro']
    titulo_mes = f"{nomes_meses[mes_alvo.month - 1]} {mes_alvo.year}"
    frotas_na_semana = list({p['id_frota'] for p in programacao_db if p.get('id_frota')})

    return render_template('programacao.html', offset=offset, mes_offset=mes_offset, modo_visualizacao=modo_visualizacao, titulo_mes=titulo_mes, calendario_mensal=calendario_mensal, dias_mes=dias_mes, data_inicio_db=data_inicio_str, data_fim_db=data_fim_str, dias_semana=dias_semana, frotas=frotas, programacao=programacao_db, stats=stats_prog, especialidades_na_semana=especialidades_na_semana, setores_na_semana=setores_na_semana, matriz_agrupada=matriz_agrupada, matriz_setor=matriz_setor, setores_cadastrados=setores_cadastrados, frentes_cadastradas=frentes_cadastradas, hoje=hoje_data.strftime('%Y-%m-%d'), frotas_na_semana=frotas_na_semana, ultima_importacao_os=ultima_importacao_os)

@app.route('/lavagem')
def lavagem():
    # Lavagem/Lubrificação nao tem mais aba/pagina propria - agora sao so mais dois
    # tipos de servico dentro da Programação Semanal. Links antigos continuam funcionando.
    return redirect(url_for('programacao'), code=301)

def _lavagem_legado():
    if not session.get('logado'): return redirect(url_for('login'))
    if not tem_acesso_modulo('lavagem'):
        flash('Seu perfil não tem permissão para acessar Lavagem / Lubrificação.', 'danger')
        return redirect(url_for('admin'))

    offset = int(request.args.get('offset', 0))
    hoje_data = datetime.now()
    segunda_atual = hoje_data - timedelta(days=hoje_data.weekday())
    segunda_alvo = segunda_atual + timedelta(weeks=offset)
    domingo_alvo = segunda_alvo + timedelta(days=6)
    data_inicio_str = segunda_alvo.strftime('%Y-%m-%d')
    data_fim_str = domingo_alvo.strftime('%Y-%m-%d')

    conn = get_db_connection()
    frotas = conn.execute('SELECT id_frota, descricao FROM frotas ORDER BY id_frota').fetchall()
    setores_rows = conn.execute('''
        SELECT setor, id_frota
        FROM frotas
        WHERE setor IS NOT NULL AND TRIM(setor) != ''
        ORDER BY setor, id_frota
    ''').fetchall()
    dias_semana = get_dias_semana(segunda_alvo)
    programacao_db = get_programacao_agrupada(conn, data_inicio_str, data_fim_str)
    programacao_db = [p for p in programacao_db if ('LAVAGEM' in (p.get('tipo_servico') or '') or 'LUBRIFICACAO' in (p.get('tipo_servico') or ''))]
    stats_prog = calcular_stats_prog(programacao_db)
    especialidades_na_semana = get_especialidades_da_semana(programacao_db)
    setores_na_semana = get_setores_da_semana(programacao_db)

    setores_cadastrados = []
    setor_atual = None
    for row in setores_rows:
        nome_setor = row['setor'].strip()
        if not setor_atual or setor_atual['nome'] != nome_setor:
            setor_atual = {'nome': nome_setor, 'frotas': []}
            setores_cadastrados.append(setor_atual)
        setor_atual['frotas'].append(row['id_frota'])

    matriz_agrupada = {}
    matriz_setor = {}
    for p in programacao_db:
        macro = p['especialidade'] if p['especialidade'] else 'GERAL'
        micro = p['especialidade_detalhe'] if p['especialidade_detalhe'] else 'OUTROS'
        if macro not in matriz_agrupada:
            matriz_agrupada[macro] = set()
        matriz_agrupada[macro].add(micro)

        setor = p.get('setor', 'SEM SETOR') or 'SEM SETOR'
        if setor not in matriz_setor:
            matriz_setor[setor] = set()
        matriz_setor[setor].add(micro)

    matriz_agrupada = {k: sorted(list(v)) for k, v in sorted(matriz_agrupada.items())}
    matriz_setor = {k: sorted(list(v)) for k, v in sorted(matriz_setor.items())}
    conn.close()

    return render_template('lavagem.html', offset=offset, data_inicio_db=data_inicio_str, data_fim_db=data_fim_str, dias_semana=dias_semana, frotas=frotas, programacao=programacao_db, stats=stats_prog, especialidades_na_semana=especialidades_na_semana, setores_na_semana=setores_na_semana, matriz_agrupada=matriz_agrupada, matriz_setor=matriz_setor, setores_cadastrados=setores_cadastrados, hoje=hoje_data.strftime('%Y-%m-%d'))

@app.route('/api/agendar_servico', methods=['POST'])
def agendar_servico():
    if not session.get('logado'): return redirect(url_for('login'))
    frota = request.form.get('frota').strip().upper()
    tipo = request.form.get('tipo')
    if tipo in ['LAVAGEM', 'LUBRIFICACAO']:
        if not tem_acesso_modulo('lavagem'):
            flash('Sem permissão para agendar Lavagem / Lubrificação.', 'danger')
            return redirect(url_for('admin'))
    else:
        if not tem_acesso_modulo('programacao'):
            flash('Sem permissão para agendar nesta programação.', 'danger')
            return redirect(url_for('admin'))
    data = request.form.get('data')
    observacao = request.form.get('observacao', '').strip().upper()
    conn = get_db_connection()
    existente = conn.execute('SELECT id FROM programacao_semanal WHERE id_frota = ? AND tipo_servico = ? AND data_planejada = ? AND observacao = ?', (frota, tipo, data, observacao)).fetchone()
    if existente:
        data_formatada = f"{data[8:10]}/{data[5:7]}"
        flash(f'Atenção: A frota {frota} já tem uma {tipo} com essa exata observação agendada para o dia {data_formatada}!', 'warning')
    else:
        conn.execute('INSERT INTO programacao_semanal (id_frota, tipo_servico, data_planejada, observacao) VALUES (?, ?, ?, ?)', (frota, tipo, data, observacao))
        flash(f'Serviço agendado para a frota {frota}!', 'success')
    conn.commit()
    conn.close()
    return redirect(request.referrer or url_for('programacao'))

@app.route('/api/agendar_lote', methods=['POST'])
def agendar_lote():
    if not session.get('logado'): return redirect(url_for('login'))

    frotas_raw = request.form.get('frotas', '')
    tipo = request.form.get('tipo')
    if tipo in ['LAVAGEM', 'LUBRIFICACAO']:
        if not tem_acesso_modulo('lavagem'):
            flash('Sem permissão para agendamento em lote de Lavagem / Lubrificação.', 'danger')
            return redirect(url_for('admin'))
    else:
        if not tem_acesso_modulo('programacao'):
            flash('Sem permissão para agendamento em lote.', 'danger')
            return redirect(url_for('admin'))
    observacao = request.form.get('observacao', '').strip().upper()
    datas = request.form.getlist('datas')

    destino = request.referrer or url_for('programacao')

    if not datas:
        flash('Atenção: Tem de selecionar pelo menos um dia da semana para distribuir as frotas!', 'danger')
        return redirect(destino)

    grupos_frotas = extrair_grupos_frotas_lote(frotas_raw)
    frotas_limpas = [normalizar_id_frota(' - '.join(grupo)) for grupo in grupos_frotas if grupo]
    frotas_unicas = list(dict.fromkeys(frotas_limpas))

    if not frotas_unicas:
        flash('Nenhuma frota válida encontrada no bloco de texto.', 'danger')
        return redirect(destino)

    conn = get_db_connection()
    aplicar_setor_primeira_frota_lote(conn, grupos_frotas)
    sucesso = 0
    ignoradas = 0
    num_dias = len(datas)

    for i, frota in enumerate(frotas_unicas):
        # Se a frota já tem algo agendado em um dos dias selecionados (de um lote anterior,
        # ex: troca de óleo do motor), reaproveita esse mesmo dia para que os serviços se
        # agrupem no mesmo card em vez de espalhar a frota em dias diferentes.
        placeholders = ','.join('?' * len(datas))
        ja_agendada = conn.execute(
            f'SELECT data_planejada FROM programacao_semanal WHERE id_frota = ? AND data_planejada IN ({placeholders}) LIMIT 1',
            [frota] + datas
        ).fetchone()
        data_alvo = ja_agendada['data_planejada'] if ja_agendada else datas[i % num_dias]

        existente = conn.execute('SELECT id FROM programacao_semanal WHERE id_frota = ? AND tipo_servico = ? AND data_planejada = ? AND observacao = ?', (frota, tipo, data_alvo, observacao)).fetchone()
        if existente:
            ignoradas += 1
        else:
            conn.execute('INSERT INTO programacao_semanal (id_frota, tipo_servico, data_planejada, observacao) VALUES (?, ?, ?, ?)', (frota, tipo, data_alvo, observacao))
            sucesso += 1

    conn.commit()
    conn.close()

    if sucesso > 0:
        flash(f'Fantástico! {sucesso} frotas foram agendadas e distribuídas com sucesso ao longo de {num_dias} dia(s)!', 'success')
    if ignoradas > 0:
        flash(f'Atenção: {ignoradas} frotas foram ignoradas pois já tinham esse serviço marcado no dia.', 'warning')

    return redirect(request.referrer or url_for('programacao'))

@app.route('/api/atualizar_servico_lote', methods=['POST'])
def atualizar_servico_lote():
    if not session.get('logado'): return {"status": "erro", "msg": "Sem permissão"}, 403
    if not (tem_acesso_modulo('programacao') or tem_acesso_modulo('lavagem')): return {"status": "erro", "msg": "Sem permissão"}, 403
    dados = request.get_json()
    data_execucao = dados.get('data_execucao')
    local_execucao = dados.get('local_execucao', 'BASE')
    id_frota = dados.get('id_frota')
    bloquear_oficina = dados.get('bloquear_oficina', False)
    liberar_oficina = dados.get('liberar_oficina', False)
    atualizacoes = dados.get('atualizacoes', [])

    conn = get_db_connection()
    data_atual = datetime.now().strftime("%d/%m/%Y")
    tem_novo_bloqueio = False
    motivos_bloqueio_novos = []
    servicos_resolvidos = []

    for at in atualizacoes:
        id_p = at['id']
        novo_status = at['status']
        tipo = at['tipo_servico']
        resultado = at.get('resultado', '')
        observacao = at.get('observacao', '').strip().upper()
        execucao = data_execucao if novo_status == 'REALIZADA' else None
        loc = local_execucao if novo_status == 'REALIZADA' else 'BASE'

        # Edição manual "reivindica" a linha: origem_classificacao volta pra
        # MANUAL, pra deixar claro que não foi mais a importação de O.S. que
        # decidiu esse status por último (é só rótulo pro painel de
        # transparência - quem trava reclassificação automática é o status
        # BLOQUEADA em si, em classificar_programacao).
        conn.execute('''UPDATE programacao_semanal
                         SET status = ?, data_execucao = ?, local_execucao = ?,
                             resultado_analise = ?, observacao = ?,
                             origem_classificacao = 'MANUAL'
                         WHERE id = ?''',
                     (novo_status, execucao, loc, resultado, observacao, id_p))

        if novo_status in ['BLOQUEADA', 'QUEBRADA'] or resultado == 'CRÍTICO':
            tem_novo_bloqueio = True
            if resultado == 'CRÍTICO': motivos_bloqueio_novos.append('ANÁLISE DE ÓLEO CRÍTICA')
            else: motivos_bloqueio_novos.append(tipo)

        elif novo_status == 'REALIZADA':
            servicos_resolvidos.append(tipo)
            servicos_resolvidos.append('ANÁLISE DE ÓLEO CRÍTICA')

    ja_bloqueada = conn.execute('SELECT id, motivo FROM historico_bloqueios WHERE id_frota = ? AND data_desbloqueio IS NULL', (id_frota,)).fetchone()

    if ja_bloqueada:
        motivos_atuais = [m.strip() for m in ja_bloqueada['motivo'].split('+') if m.strip()]
        if liberar_oficina:
            motivos_restantes = [m for m in motivos_atuais if m not in servicos_resolvidos]
            motivos_atuais = motivos_restantes
            if len(motivos_atuais) == 0:
                conn.execute('UPDATE historico_bloqueios SET data_desbloqueio = ?, observacao = ? WHERE id = ?', (data_atual, 'Liberado via Programação Semanal', ja_bloqueada['id']))
            else:
                conn.execute('UPDATE historico_bloqueios SET motivo = ? WHERE id = ?', (" + ".join(motivos_atuais), ja_bloqueada['id']))

        if tem_novo_bloqueio and bloquear_oficina:
            blq_state = conn.execute('SELECT data_desbloqueio FROM historico_bloqueios WHERE id = ?', (ja_bloqueada['id'],)).fetchone()
            if blq_state['data_desbloqueio'] is None:
                for novo in motivos_bloqueio_novos:
                    if novo not in motivos_atuais: motivos_atuais.append(novo)
                conn.execute('UPDATE historico_bloqueios SET motivo = ? WHERE id = ?', (" + ".join(motivos_atuais), ja_bloqueada['id']))
            else:
                conn.execute('INSERT INTO historico_bloqueios (id_frota, motivo, status_oficina, data_bloqueio) VALUES (?, ?, ?, ?)', (id_frota, " + ".join(motivos_bloqueio_novos), 'AGUARDANDO EQUIP.', data_atual))
    else:
        if tem_novo_bloqueio and bloquear_oficina:
            conn.execute('INSERT INTO historico_bloqueios (id_frota, motivo, status_oficina, data_bloqueio) VALUES (?, ?, ?, ?)', (id_frota, " + ".join(motivos_bloqueio_novos), 'AGUARDANDO EQUIP.', data_atual))

    conn.commit()
    conn.close()
    return {"status": "sucesso"}

@app.route('/api/excluir_servico/<string:ids_prog>', methods=['POST'])
def excluir_servico(ids_prog):
    if not session.get('logado'): return {"status": "erro", "msg": "Sem permissão"}, 403
    if not (tem_acesso_modulo('programacao') or tem_acesso_modulo('lavagem')): return {"status": "erro", "msg": "Sem permissão"}, 403
    conn = get_db_connection()
    lista_ids = ids_prog.split(',')
    for id_p in lista_ids: conn.execute('DELETE FROM programacao_semanal WHERE id = ?', (id_p,))
    conn.commit()
    conn.close()
    return {"status": "sucesso"}

@app.route('/api/mover_servico/<string:ids_prog>', methods=['POST'])
def mover_servico(ids_prog):
    if not session.get('logado'): return {"status": "erro", "msg": "Sem permissão"}, 403
    if not (tem_acesso_modulo('programacao') or tem_acesso_modulo('lavagem')): return {"status": "erro", "msg": "Sem permissão"}, 403
    dados = request.get_json()
    nova_data = dados.get('nova_data')
    conn = get_db_connection()
    lista_ids = ids_prog.split(',')
    for id_p in lista_ids:
        servico = conn.execute('SELECT id_frota, tipo_servico, observacao FROM programacao_semanal WHERE id = ?', (id_p,)).fetchone()
        if servico:
            frota = servico['id_frota']
            tipo = servico['tipo_servico']
            obs = servico['observacao']

            existente = conn.execute('SELECT id FROM programacao_semanal WHERE id_frota = ? AND tipo_servico = ? AND data_planejada = ? AND observacao = ? AND id != ?', (frota, tipo, nova_data, obs, id_p)).fetchone()

            if existente: conn.execute('DELETE FROM programacao_semanal WHERE id = ?', (id_p,))
            else: conn.execute('UPDATE programacao_semanal SET data_planejada = ? WHERE id = ?', (nova_data, id_p))
    conn.commit()
    conn.close()
    return {"status": "sucesso"}

@app.route('/relatorio_programacao')
def relatorio_programacao():
    if not session.get('logado'): return redirect(url_for('login'))
    data_inicio = request.args.get('inicio')
    data_fim = request.args.get('fim')
    layout = request.args.get('layout', 'agrupada')
    tipos_selecionados = request.args.getlist('tipos')
    if not tipos_selecionados: tipos_selecionados = ['TROCA DE OLEO', 'PREVENTIVA', 'ANALISE DE OLEO', 'PIT STOP', 'SOPRAGEM DE FILTROS']

    # Os PDFs de calendario (Lista Agrupada / Calendario Separado) sempre trazem o MES
    # COMPLETO, mesmo que a tela interativa esteja aberta numa semana especifica - assim
    # da pra mandar um unico arquivo com a programacao inteira do mes nos grupos. A tela
    # de atualizacao (drag-and-drop) continua semanal, sem nenhuma mudanca aqui.
    ref_data = datetime.strptime(data_inicio, '%Y-%m-%d')
    usa_mes_completo = layout in ('agrupada', 'matriz')
    semanas_mes = []
    if usa_mes_completo:
        primeiro_mes = ref_data.replace(day=1)
        if primeiro_mes.month == 12:
            proximo_mes = primeiro_mes.replace(year=primeiro_mes.year + 1, month=1)
        else:
            proximo_mes = primeiro_mes.replace(month=primeiro_mes.month + 1)
        ultimo_mes = proximo_mes - timedelta(days=1)
        data_inicio_consulta = primeiro_mes.strftime('%Y-%m-%d')
        data_fim_consulta = ultimo_mes.strftime('%Y-%m-%d')
        semanas_mes = get_calendario_mensal(primeiro_mes)
    else:
        data_inicio_consulta = data_inicio
        data_fim_consulta = data_fim

    conn = get_db_connection()
    programacao_db = get_programacao_agrupada(conn, data_inicio_consulta, data_fim_consulta)
    conn.close()

    prog_filtrada = [p for p in programacao_db if any(t in p['tipo_servico'] for t in tipos_selecionados)]

    # O PDF respeita os filtros que estavam aplicados na tela (setor e status).
    filtro_setor = (request.args.get('setor') or '').strip()
    if filtro_setor and filtro_setor.upper() != 'TODOS':
        prog_filtrada = [p for p in prog_filtrada if (p.get('setor') or 'SEM SETOR') == filtro_setor]

    filtro_status = (request.args.get('status') or '').strip().upper()
    if filtro_status and filtro_status != 'TODOS':
        prog_filtrada = [p for p in prog_filtrada if (p.get('status') or 'PENDENTE').upper() == filtro_status]

    dias_semana = get_dias_semana(ref_data - timedelta(days=ref_data.weekday()))

    # Agrupamento por SETOR (nao mais por tipo/especialidade de equipamento), a pedido:
    # cada setor consegue ver so os equipamentos programados dentro dele.
    setores_pdf = sorted({(p.get('setor') or 'SEM SETOR') for p in prog_filtrada})
    agrupado_pdf = {}
    for p in prog_filtrada:
        setor = p.get('setor') or 'SEM SETOR'
        agrupado_pdf.setdefault(setor, []).append(p)
    agrupado_pdf = {k: sorted(agrupado_pdf[k], key=lambda f: (f['data_planejada'], f['id_frota'])) for k in sorted(agrupado_pdf)}

    nomes_bonitos = []
    if 'TROCA DE OLEO' in tipos_selecionados: nomes_bonitos.append('Troca de Óleo')
    if 'PREVENTIVA' in tipos_selecionados: nomes_bonitos.append('Preventiva')
    if 'ANALISE DE OLEO' in tipos_selecionados: nomes_bonitos.append('Análise de Óleo')
    if 'PIT STOP' in tipos_selecionados: nomes_bonitos.append('Pit Stop')
    if 'SOPRAGEM DE FILTROS' in tipos_selecionados: nomes_bonitos.append('Sopragem de Filtros')
    titulo_tipos = " + ".join(nomes_bonitos)

    if usa_mes_completo:
        nomes_meses = ['Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho', 'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro']
        periodo = f"{nomes_meses[primeiro_mes.month - 1]} de {primeiro_mes.year} — mês completo"
    else:
        data_inicio_br = datetime.strptime(data_inicio, '%Y-%m-%d').strftime('%d/%m/%Y')
        data_fim_br = datetime.strptime(data_fim, '%Y-%m-%d').strftime('%d/%m/%Y')
        periodo = f"{data_inicio_br} a {data_fim_br}"
    data_geracao = datetime.now().strftime('%d/%m/%Y %H:%M')

    return render_template('relatorio_programacao.html', programacao=prog_filtrada, dias_semana=dias_semana, setores=setores_pdf, semanas_mes=semanas_mes, layout=layout, titulo=f"Fila de Programação: {titulo_tipos}", periodo=periodo, data_geracao=data_geracao, agrupado_pdf=agrupado_pdf)

# ── Dashboards por tipo de servico: dados compartilhados ──────────────────────
# Usado pelas paginas dash_oleo/dash_analise/dash_preventiva/dash_pitstop (relatorio
# oficial em PDF) e pela API JSON que alimenta o balao expandido dentro da
# Programacao Semanal / Transparencia, para os dois lugares mostrarem exatamente
# os mesmos numeros com os mesmos graficos.
_DASH_TIPO_CONFIG = {
    'oleo':       {'like': '%TROCA DE OLEO%',   'titulo': 'Troca de Óleo',     'com_historico': True},
    'analise':    {'like': '%ANALISE DE OLEO%', 'titulo': 'Análise de Óleo',   'com_historico': True},
    'preventiva': {'like': '%PREVENTIVA%',      'titulo': 'Preventiva',        'com_historico': True},
    'pitstop':    {'like': '%PIT STOP%',        'titulo': 'Pit Stop',          'com_historico': True},
}

def _montar_dashboard_tipo(like_pattern, data_inicio, data_fim, segunda, com_historico=True):
    conn = get_db_connection()
    # O motivo/situação vem do bloqueio ABERTO da frota (historico_bloqueios sem
    # data_desbloqueio): é o que responde "por que essa frota não fez a preventiva".
    rows = conn.execute('''
        SELECT p.id, p.id_frota,
               COALESCE(NULLIF(TRIM(f.descricao), ''), 'SEM DESCRIÇÃO') as descricao,
               COALESCE(NULLIF(TRIM(f.agrupamento), ''), 'GERAL') as especialidade,
               COALESCE(NULLIF(TRIM(f.setor), ''), 'SEM SETOR') as setor,
               p.tipo_servico, p.status, p.data_planejada, p.data_execucao,
               IFNULL(p.resultado_analise, '') as resultado_analise,
               COALESCE(NULLIF(TRIM(p.local_execucao), ''), 'BASE') as local_execucao,
               IFNULL(b.motivo, '')         as motivo_bloqueio,
               IFNULL(b.status_oficina, '') as situacao_oficina,
               IFNULL(b.data_bloqueio, '')  as desde_bloqueio
        FROM programacao_semanal p
        LEFT JOIN frotas f ON p.id_frota = f.id_frota
        LEFT JOIN (
            SELECT id_frota, motivo, status_oficina, data_bloqueio
            FROM historico_bloqueios
            WHERE data_desbloqueio IS NULL
            GROUP BY id_frota
        ) b ON b.id_frota = p.id_frota
        WHERE p.data_planejada BETWEEN ? AND ? AND p.tipo_servico LIKE ?
    ''', (data_inicio, data_fim, like_pattern)).fetchall()

    dados = [dict(r) for r in rows]
    total = len(dados)
    realizadas = sum(1 for r in dados if r['status'] == 'REALIZADA')
    bloqueadas = sum(1 for r in dados if r['status'] == 'BLOQUEADA')
    quebradas = sum(1 for r in dados if r['status'] == 'QUEBRADA')
    pendentes = total - realizadas - bloqueadas - quebradas
    valido = total - quebradas
    resumo = {
        'total': total, 'realizadas': realizadas, 'bloqueadas': bloqueadas,
        'quebradas': quebradas, 'pendentes': pendentes,
        'percentual': int((realizadas / valido) * 100) if valido > 0 else 0,
    }

    historico = []
    if com_historico:
        nome_meses = ['JAN', 'FEV', 'MAR', 'ABR', 'MAI', 'JUN', 'JUL', 'AGO', 'SET', 'OUT', 'NOV', 'DEZ']
        for i in range(4, -1, -1):
            seg_hist = segunda - timedelta(weeks=i)
            dom_hist = seg_hist + timedelta(days=6)
            rows_semana = conn.execute('''
                SELECT status FROM programacao_semanal
                WHERE data_planejada BETWEEN ? AND ? AND tipo_servico LIKE ? AND status != 'QUEBRADA'
            ''', (seg_hist.strftime('%Y-%m-%d'), dom_hist.strftime('%Y-%m-%d'), like_pattern)).fetchall()
            total_s = len(rows_semana)
            real_s = sum(1 for r in rows_semana if r['status'] == 'REALIZADA')
            perc_s = int((real_s / total_s) * 100) if total_s > 0 else 0
            mes_nome = nome_meses[seg_hist.month - 1]
            historico.append({
                'semana': f"{mes_nome} {seg_hist.strftime('%d/%m')} - {dom_hist.strftime('%d/%m')}",
                'perc': perc_s, 'realizadas': real_s
            })

    conn.close()
    return dados, resumo, historico

def _periodo_dashboard():
    """Le inicio/fim da query string (padrao: semana atual) e devolve tambem os objetos date."""
    data_inicio = request.args.get('inicio')
    data_fim = request.args.get('fim')
    if not data_inicio or not data_fim:
        hoje = datetime.now()
        segunda = hoje - timedelta(days=hoje.weekday())
        domingo = segunda + timedelta(days=6)
        data_inicio = segunda.strftime('%Y-%m-%d')
        data_fim = domingo.strftime('%Y-%m-%d')
    else:
        segunda = datetime.strptime(data_inicio, '%Y-%m-%d')
        domingo = datetime.strptime(data_fim, '%Y-%m-%d')
    return data_inicio, data_fim, segunda, domingo

@app.route('/api/dash_dados/<tipo>')
def api_dash_dados(tipo):
    """JSON com os mesmos dados/graficos do relatorio oficial em PDF, para o balao
    expandido dentro da Programacao Semanal e da Transparencia."""
    cfg = _DASH_TIPO_CONFIG.get(tipo)
    if not cfg:
        return jsonify({'erro': 'tipo invalido'}), 404
    data_inicio, data_fim, segunda, domingo = _periodo_dashboard()
    dados, resumo, historico = _montar_dashboard_tipo(cfg['like'], data_inicio, data_fim, segunda, cfg['com_historico'])
    periodo = f"{segunda.strftime('%d/%m/%Y')} à {domingo.strftime('%d/%m/%Y')}"
    return jsonify({
        'tipo': tipo, 'titulo': cfg['titulo'], 'dados': dados, 'resumo': resumo,
        'historico': historico, 'periodo': periodo,
        'data_geracao': datetime.now().strftime('%d/%m/%Y %H:%M:%S'),
        'data_inicio': data_inicio, 'data_fim': data_fim,
    })

@app.route('/dash_oleo')
def dash_oleo():
    data_inicio, data_fim, segunda, domingo = _periodo_dashboard()
    dados_oleo, resumo, _hist = _montar_dashboard_tipo('%TROCA DE OLEO%', data_inicio, data_fim, segunda, com_historico=False)
    periodo = f"{segunda.strftime('%d/%m/%Y')} à {domingo.strftime('%d/%m/%Y')}"
    data_geracao = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
    return render_template('dash_oleo.html', dados=dados_oleo, resumo=resumo, periodo=periodo, data_geracao=data_geracao, data_inicio=data_inicio, data_fim=data_fim)

@app.route('/dash_analise')
def dash_analise():
    data_inicio, data_fim, segunda, domingo = _periodo_dashboard()
    dados_analise, resumo, _hist = _montar_dashboard_tipo('%ANALISE DE OLEO%', data_inicio, data_fim, segunda, com_historico=False)
    periodo = f"{segunda.strftime('%d/%m/%Y')} à {domingo.strftime('%d/%m/%Y')}"
    data_geracao = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
    return render_template('dash_analise.html', dados=dados_analise, resumo=resumo, periodo=periodo, data_geracao=data_geracao, data_inicio=data_inicio, data_fim=data_fim)

@app.route('/dash_preventiva')
def dash_preventiva():
    data_inicio, data_fim, segunda, domingo = _periodo_dashboard()
    dados_prev, resumo, historico = _montar_dashboard_tipo('%PREVENTIVA%', data_inicio, data_fim, segunda, com_historico=True)
    periodo = f"{segunda.strftime('%d/%m/%Y')} à {domingo.strftime('%d/%m/%Y')}"
    data_geracao = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
    return render_template('dash_preventiva.html', dados=dados_prev, resumo=resumo, periodo=periodo, data_geracao=data_geracao, data_inicio=data_inicio, data_fim=data_fim, historico=historico)


@app.route('/dash_pitstop')
def dash_pitstop():
    data_inicio, data_fim, segunda, domingo = _periodo_dashboard()
    dados_pit, resumo, historico = _montar_dashboard_tipo('%PIT STOP%', data_inicio, data_fim, segunda, com_historico=True)
    periodo = f"{segunda.strftime('%d/%m/%Y')} à {domingo.strftime('%d/%m/%Y')}"
    data_geracao = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
    return render_template('dash_pitstop.html', dados=dados_pit, resumo=resumo,
                           periodo=periodo, data_geracao=data_geracao,
                           data_inicio=data_inicio, data_fim=data_fim, historico=historico)


@app.route('/externos')
def externos():
    if not session.get('logado'): return redirect(url_for('login'))
    if not tem_acesso_modulo('externos'):
        flash('Seu perfil não tem permissão para acessar Serviços Externos.', 'danger')
        return redirect(url_for('admin'))
    conn = get_db_connection()

    linhas = conn.execute('''
        SELECT se.*,
               COALESCE(NULLIF(TRIM(f.especialidade), ''), 'OUTROS') as especialidade,
               COALESCE(NULLIF(TRIM(f.agrupamento), ''), 'GERAL') as agrupamento,
               COALESCE(NULLIF(TRIM(f.descricao), ''), 'SEM DESCRIÇÃO') as descricao
        FROM servicos_externos se LEFT JOIN frotas f ON se.id_frota = f.id_frota
        WHERE se.status_os IN ('A', 'E') ORDER BY se.data_abertura ASC
    ''').fetchall()

    agrupado = {}
    hoje = datetime.now().date()

    segunda_atual = hoje - timedelta(days=hoje.weekday())
    domingo_atual = segunda_atual + timedelta(days=6)

    total_os = 0; soma_dias = 0; atrasadas = 0; sem_pedido = 0; na_semana = 0; proximas_semanas = 0
    sem_previsao_count = 0

    lista_modal_atrasadas = []
    lista_modal_sem_pedido = []
    lista_modal_entregas = []
    lista_modal_todas = []
    lista_modal_sem_previsao = []

    fornecedores_dias = {}; especialidades_dias = {}; funil = {'Aguardando Orçamento': 0, 'Em Análise': 0, 'Em Execução (Pedido OK)': 0}
    for row in linhas:
        macro = row['agrupamento'].strip() if row['agrupamento'] and row['agrupamento'].strip() else 'GERAL'
        detalhe = row['especialidade'].strip() if row['especialidade'] and row['especialidade'].strip() else 'OUTROS'
        if macro not in agrupado: agrupado[macro] = {}
        if detalhe not in agrupado[macro]: agrupado[macro][detalhe] = []
        data_db = row['data_abertura'][:10]
        try:
            data_obj = datetime.strptime(data_db, '%Y-%m-%d')
            data_br = data_obj.strftime('%d/%m/%Y')
            dias = (hoje - data_obj.date()).days
        except: data_br = data_db; dias = 0

        total_os += 1; soma_dias += dias

        if row['pedido'] == 0:
            sem_pedido += 1
            if row['orcamento'] == 0: funil['Aguardando Orçamento'] += 1
            else: funil['Em Análise'] += 1
        else: funil['Em Execução (Pedido OK)'] += 1

        previsao = row['previsao_atual']
        is_atrasada = False
        is_na_semana = False
        is_proxima = False
        is_entrega = False
        prev_formatada = 'Sem Previsão'
        offset_semana = 0

        if previsao:
            try:
                prev_date = datetime.strptime(previsao, '%Y-%m-%d').date()
                prev_formatada = prev_date.strftime('%d/%m/%Y')
                if prev_date < hoje:
                    atrasadas += 1; is_atrasada = True
                elif segunda_atual <= prev_date <= domingo_atual:
                    na_semana += 1; is_na_semana = True; is_entrega = True
                    offset_semana = 0
                elif prev_date > domingo_atual:
                    proximas_semanas += 1; is_proxima = True; is_entrega = True
                    offset_semana = (prev_date - segunda_atual).days // 7
            except: pass

        info_modal = {
            'os': row['nro_os'],
            'frota': row['id_frota'],
            'descricao': row['descricao'],
            'fornecedor': row['fornecedor'] or 'NÃO INFORMADO',
            'data_abertura': data_br,
            'dias': dias,
            'orcamento': row['orcamento'],
            'solicitacao': row['solicitacao'],
            'cotacao': row['cotacao'],
            'pedido': row['pedido'],
            'previsao': prev_formatada,
            'previsao_vencida': is_atrasada,
            'is_na_semana': is_na_semana,
            'is_proxima': is_proxima,
            'offset_semana': offset_semana
        }

        lista_modal_todas.append(info_modal)
        if row['pedido'] == 0: lista_modal_sem_pedido.append(info_modal)
        if is_atrasada: lista_modal_atrasadas.append(info_modal)
        if is_entrega: lista_modal_entregas.append(info_modal)

        if not previsao:
            sem_previsao_count += 1
            lista_modal_sem_previsao.append(info_modal)

        forn = row['fornecedor'] or 'NÃO INFORMADO'
        if forn not in fornecedores_dias: fornecedores_dias[forn] = {'soma': 0, 'count': 0}
        fornecedores_dias[forn]['soma'] += dias; fornecedores_dias[forn]['count'] += 1
        if macro not in especialidades_dias: especialidades_dias[macro] = {'soma': 0, 'count': 0}
        especialidades_dias[macro]['soma'] += dias; especialidades_dias[macro]['count'] += 1
        hist_rows = conn.execute("SELECT * FROM historico_previsoes_externas WHERE nro_os = ? ORDER BY id DESC", (row['nro_os'],)).fetchall()
        historico = [{'data': r['data_registro'], 'previsao': r['previsao']} for r in hist_rows]
        agrupado[macro][detalhe].append({'nro_os': row['nro_os'], 'frota': row['id_frota'], 'descricao': row['descricao'], 'especialidade_detalhe': detalhe, 'fornecedor': row['fornecedor'], 'data_abertura': data_br, 'dias': dias, 'orcamento': row['orcamento'], 'solicitacao': row['solicitacao'], 'cotacao': row['cotacao'], 'pedido': row['pedido'], 'previsao_atual': previsao, 'is_atrasada': is_atrasada, 'historico': historico, 'observacao': row['observacao'] or '',
            'orcamento_numero': row['orcamento_numero'] or '', 'solicitacao_numero': row['solicitacao_numero'] or '', 'cotacao_numero': row['cotacao_numero'] or '', 'pedido_numero': row['pedido_numero'] or ''})

    linhas_hist = conn.execute('''
        SELECT se.*, f.descricao
        FROM servicos_externos se
        LEFT JOIN frotas f ON se.id_frota = f.id_frota
        WHERE se.status_os = 'F'
        ORDER BY IFNULL(se.data_fechamento, se.data_abertura) DESC LIMIT 150
    ''').fetchall()

    historico_finalizadas = []
    for r in linhas_hist:
        hist_rows = conn.execute("SELECT * FROM historico_previsoes_externas WHERE nro_os = ? ORDER BY id DESC", (r['nro_os'],)).fetchall()
        logs = [{'data': hr['data_registro'], 'previsao': hr['previsao']} for hr in hist_rows]
        try:
            data_br = datetime.strptime(r['data_abertura'][:10], '%Y-%m-%d').strftime('%d/%m/%Y')
        except: data_br = r['data_abertura']

        try:
            if r['data_fechamento']:
                data_fechamento_br = datetime.strptime(r['data_fechamento'][:10], '%Y-%m-%d').strftime('%d/%m/%Y')
            else:
                data_fechamento_br = '-'
        except: data_fechamento_br = r.get('data_fechamento', '-')

        historico_finalizadas.append({
            'nro_os': r['nro_os'],
            'frota': r['id_frota'],
            'descricao': r['descricao'] or 'SEM DESCRIÇÃO',
            'fornecedor': r['fornecedor'],
            'data_abertura': data_br,
            'data_fechamento': data_fechamento_br,
            'logs': logs
        })

    conn.close()

    tmet = int(soma_dias / total_os) if total_os > 0 else 0
    forn_stats = []; esp_stats = []
    for f, d in fornecedores_dias.items(): forn_stats.append({'fornecedor': f[:20], 'volume': d['count'], 'media': round(d['soma'] / d['count'], 1)})
    forn_volume = sorted(forn_stats, key=lambda x: x['volume'], reverse=True)[:5]
    forn_lentos = sorted(forn_stats, key=lambda x: x['media'], reverse=True)[:5]
    for e, d in especialidades_dias.items(): esp_stats.append({'especialidade': e, 'media': round(d['soma'] / d['count'], 1)})
    esp_stats = sorted(esp_stats, key=lambda x: x['media'], reverse=True)

    stats = {
        'total_os': total_os, 'tmet': tmet, 'atrasadas': atrasadas, 'sem_pedido': sem_pedido,
        'na_semana': na_semana, 'proximas_semanas': proximas_semanas, 'sem_previsao_count': sem_previsao_count,
        'forn_volume': forn_volume, 'forn_lentos': forn_lentos, 'esp_media': esp_stats, 'funil': funil,
        'detalhes': {
            'todas': lista_modal_todas,
            'atrasadas': lista_modal_atrasadas,
            'sem_pedido': lista_modal_sem_pedido,
            'entregas': sorted(lista_modal_entregas, key=lambda x: datetime.strptime(x['previsao'], '%d/%m/%Y')),
            'sem_previsao': lista_modal_sem_previsao
        }
    }

    os_fechar = session.pop('os_para_fechar', None)
    data_hoje_str = datetime.now().strftime('%Y-%m-%d')

    return render_template('externos.html', agrupado=agrupado, stats=stats, historico_finalizadas=historico_finalizadas, os_fechar=os_fechar, data_hoje=data_hoje_str)

@app.route('/ferramentaria')
def ferramentaria():
    if not session.get('logado'): return redirect(url_for('login'))
    if not tem_acesso_modulo('ferramentaria'):
        flash('Seu perfil não tem permissão para acessar Ferramentaria.', 'danger')
        return redirect(url_for('admin'))
    conn = get_db_connection()
    busca_ferramenta = request.args.get('busca_ferramenta', '').strip()
    try:
        pagina_ferramentas = int(request.args.get('pagina_ferramentas', 1))
    except:
        pagina_ferramentas = 1
    if pagina_ferramentas < 1:
        pagina_ferramentas = 1
    por_pagina_ferramentas = 100
    active_tab = request.args.get('aba', '').strip()
    if active_tab not in ['retirada', 'historico', 'base', 'estoque', 'analitico', 'filtros', 'agregados', 'caixas', 'caminhoes', 'conferencia']:
        active_tab = 'base' if busca_ferramenta else 'analitico'

    # Filtros do Histórico de Retiradas
    hist_mecanico = request.args.get('hist_mecanico', '').strip()
    hist_frota = request.args.get('hist_frota', '').strip()
    hist_ferramenta = request.args.get('hist_ferramenta', '').strip()
    hist_status = request.args.get('hist_status', '').strip()  # '', 'aberto', 'devolvido'
    hist_data_inicio = request.args.get('hist_data_inicio', '').strip()
    hist_data_fim = request.args.get('hist_data_fim', '').strip()
    hist_tem_filtro = bool(hist_mecanico or hist_frota or hist_ferramenta or hist_status or hist_data_inicio or hist_data_fim)

    hist_where = []
    hist_params = []
    if hist_mecanico:
        hist_where.append('r.mecanico LIKE ?')
        hist_params.append(f'%{hist_mecanico.upper()}%')
    if hist_frota:
        hist_where.append('r.frota LIKE ?')
        hist_params.append(f'%{hist_frota.upper()}%')
    if hist_status == 'aberto':
        hist_where.append('r.data_devolucao IS NULL')
    elif hist_status == 'devolvido':
        hist_where.append('r.data_devolucao IS NOT NULL')
    if hist_data_inicio:
        hist_where.append('r.data_retirada >= ?')
        hist_params.append(hist_data_inicio)
    if hist_data_fim:
        hist_where.append('r.data_retirada <= ?')
        hist_params.append(hist_data_fim)
    if hist_ferramenta:
        hist_where.append('''r.id IN (
            SELECT retirada_id FROM ferramentaria_retirada_itens
            WHERE codigo LIKE ? OR descricao LIKE ?
        )''')
        hist_params.append(f'%{hist_ferramenta.upper()}%')
        hist_params.append(f'%{hist_ferramenta.upper()}%')

    hist_where_sql = (' WHERE ' + ' AND '.join(hist_where)) if hist_where else ''
    # Sem filtro: só os 150 mais recentes (performance). Com filtro: busca em todo o histórico.
    hist_limit_sql = '' if hist_tem_filtro else ' LIMIT 150'

    total_historico_geral = conn.execute('SELECT COUNT(*) FROM ferramentaria_retiradas').fetchone()[0]

    rows = conn.execute(f'''
        SELECT r.*,
               COUNT(i.id) as total_itens,
               COALESCE(SUM(i.quantidade * i.valor_unitario), 0) as valor_total
        FROM ferramentaria_retiradas r
        LEFT JOIN ferramentaria_retirada_itens i ON i.retirada_id = r.id
        {hist_where_sql}
        GROUP BY r.id
        ORDER BY r.data_retirada DESC, r.id DESC
        {hist_limit_sql}
    ''', hist_params).fetchall()
    historico = []
    hoje_date = datetime.now().date()
    for row in rows:
        item = dict(row)
        itens = conn.execute('''
            SELECT codigo, descricao, quantidade, valor_unitario,
                   quantidade * valor_unitario as valor_total
            FROM ferramentaria_retirada_itens
            WHERE retirada_id = ?
            ORDER BY id
        ''', (row['id'],)).fetchall()
        item['itens'] = []
        for i in itens:
            item_dict = dict(i)
            item_dict['quantidade_fmt'] = formatar_quantidade_ferramenta(item_dict.get('quantidade'))
            item['itens'].append(item_dict)
        if not item.get('data_devolucao'):
            try:
                dr = datetime.strptime(item['data_retirada'][:10], '%Y-%m-%d').date()
                item['dias_aberto'] = (hoje_date - dr).days
            except Exception:
                item['dias_aberto'] = 0
        else:
            item['dias_aberto'] = None
        historico.append(item)

    total_ferramentas = conn.execute('SELECT COUNT(*) FROM ferramentaria_ferramentas WHERE ativo = 1').fetchone()[0]
    if busca_ferramenta:
        termo = f'%{busca_ferramenta.upper()}%'
        total_ferramentas_filtrado = conn.execute('''
            SELECT COUNT(*)
            FROM ferramentaria_ferramentas
            WHERE ativo = 1 AND (codigo LIKE ? OR descricao LIKE ?)
        ''', (termo, termo)).fetchone()[0]
        total_paginas_ferramentas = max(1, (total_ferramentas_filtrado + por_pagina_ferramentas - 1) // por_pagina_ferramentas)
        pagina_ferramentas = min(pagina_ferramentas, total_paginas_ferramentas)
        offset_ferramentas = (pagina_ferramentas - 1) * por_pagina_ferramentas
        ferramentas = conn.execute('''
            SELECT id, codigo, descricao, estoque_total, estoque_minimo
            FROM ferramentaria_ferramentas
            WHERE ativo = 1 AND (codigo LIKE ? OR descricao LIKE ?)
            ORDER BY codigo
            LIMIT ? OFFSET ?
        ''', (termo, termo, por_pagina_ferramentas, offset_ferramentas)).fetchall()
    else:
        total_ferramentas_filtrado = total_ferramentas
        total_paginas_ferramentas = max(1, (total_ferramentas_filtrado + por_pagina_ferramentas - 1) // por_pagina_ferramentas)
        pagina_ferramentas = min(pagina_ferramentas, total_paginas_ferramentas)
        offset_ferramentas = (pagina_ferramentas - 1) * por_pagina_ferramentas
        ferramentas = conn.execute('''
            SELECT id, codigo, descricao, estoque_total, estoque_minimo
            FROM ferramentaria_ferramentas
            WHERE ativo = 1
            ORDER BY id DESC
            LIMIT ? OFFSET ?
        ''', (por_pagina_ferramentas, offset_ferramentas)).fetchall()
    emprestimos_por_codigo = {
        str(r['codigo'] or ''): float(r['emprestadas'] or 0)
        for r in conn.execute('''
            SELECT codigo, SUM(quantidade) as emprestadas
            FROM ferramentaria_retirada_itens i
            JOIN ferramentaria_retiradas r ON r.id = i.retirada_id
            WHERE r.data_devolucao IS NULL
              AND codigo IS NOT NULL AND TRIM(codigo) != ''
            GROUP BY codigo
        ''').fetchall()
    }
    ferramentas_lista = []
    for f in ferramentas:
        item = dict(f)
        emprestadas = emprestimos_por_codigo.get(str(item.get('codigo') or ''), 0)
        estoque_total = float(item.get('estoque_total') or 0)
        item['emprestadas'] = emprestadas
        item['disponivel'] = estoque_total
        ferramentas_lista.append(item)

    estoque_lista = conn.execute('''
        SELECT f.id, f.codigo, f.descricao, f.estoque_total, f.estoque_minimo,
               COALESCE(e.emprestadas, 0) as emprestadas,
               f.estoque_total as disponivel
        FROM ferramentaria_ferramentas f
        LEFT JOIN (
            SELECT i.codigo, SUM(i.quantidade) as emprestadas
            FROM ferramentaria_retirada_itens i
            JOIN ferramentaria_retiradas r ON r.id = i.retirada_id
            WHERE r.data_devolucao IS NULL
              AND i.codigo IS NOT NULL AND TRIM(i.codigo) != ''
            GROUP BY codigo
        ) e ON e.codigo = f.codigo
        WHERE f.ativo = 1 AND (f.estoque_total > 0 OR COALESCE(e.emprestadas, 0) > 0)
        ORDER BY disponivel ASC, e.emprestadas DESC, f.descricao
        LIMIT 100
    ''').fetchall()

    estoque_resumo = conn.execute('''
        SELECT
            COALESCE(SUM(f.estoque_total + COALESCE(e.emprestadas, 0)), 0) as total_estoque,
            COALESCE(SUM(COALESCE(e.emprestadas, 0)), 0) as total_emprestado,
            COALESCE(SUM(f.estoque_total), 0) as total_disponivel
        FROM ferramentaria_ferramentas f
        LEFT JOIN (
            SELECT i.codigo, SUM(i.quantidade) as emprestadas
            FROM ferramentaria_retirada_itens i
            JOIN ferramentaria_retiradas r ON r.id = i.retirada_id
            WHERE r.data_devolucao IS NULL
              AND i.codigo IS NOT NULL AND TRIM(i.codigo) != ''
            GROUP BY codigo
        ) e ON e.codigo = f.codigo
        WHERE f.ativo = 1
    ''').fetchone()
    total_estoque_calc = float(estoque_resumo['total_estoque'] or 0)
    total_emprestado_calc = float(estoque_resumo['total_emprestado'] or 0)
    total_disponivel_calc = float(estoque_resumo['total_disponivel'] or 0)
    percentual_disponivel = round((total_disponivel_calc / total_estoque_calc) * 100, 1) if total_estoque_calc > 0 else 0

    top_colaboradores = [dict(r) for r in conn.execute('''
        SELECT r.mecanico, SUM(i.quantidade) as total
        FROM ferramentaria_retiradas r
        JOIN ferramentaria_retirada_itens i ON i.retirada_id = r.id
        WHERE r.data_devolucao IS NULL
        GROUP BY r.mecanico
        ORDER BY total DESC
        LIMIT 10
    ''').fetchall()]
    top_tempo = [dict(r) for r in conn.execute('''
        SELECT mecanico, MIN(data_retirada) as desde,
               CAST(julianday('now') - julianday(MIN(data_retirada)) AS INTEGER) as dias
        FROM ferramentaria_retiradas
        WHERE data_devolucao IS NULL
        GROUP BY mecanico
        ORDER BY dias DESC
        LIMIT 10
    ''').fetchall()]
    top_ferramentas = [dict(r) for r in conn.execute('''
        SELECT COALESCE(NULLIF(TRIM(codigo), ''), '-') as codigo, descricao, SUM(quantidade) as total
        FROM ferramentaria_retirada_itens i
        JOIN ferramentaria_retiradas r ON r.id = i.retirada_id
        WHERE r.data_devolucao IS NULL
        GROUP BY COALESCE(NULLIF(TRIM(codigo), ''), descricao), descricao
        ORDER BY total DESC
        LIMIT 10
    ''').fetchall()]

    # Indicadores do módulo (tudo vindo das retiradas/itens já registrados).
    _ind = conn.execute('''
        SELECT
            COUNT(*)                                                      as total_retiradas,
            SUM(CASE WHEN data_devolucao IS NULL     THEN 1 ELSE 0 END)   as em_aberto,
            SUM(CASE WHEN data_devolucao IS NOT NULL THEN 1 ELSE 0 END)   as devolvidas,
            COUNT(DISTINCT CASE WHEN data_devolucao IS NULL THEN mecanico END) as colaboradores_com_posse,
            MAX(CASE WHEN data_devolucao IS NULL
                     THEN CAST(julianday('now') - julianday(data_retirada) AS INTEGER) END) as maior_posse_dias
        FROM ferramentaria_retiradas
    ''').fetchone()

    # Itens em posse de alguém mas sem estoque cadastrado na base: o que precisa
    # de regularização/reposição no cadastro.
    _criticos = conn.execute('''
        SELECT COUNT(*) as qtd FROM (
            SELECT i.codigo
            FROM ferramentaria_retirada_itens i
            JOIN ferramentaria_retiradas r ON r.id = i.retirada_id
            WHERE r.data_devolucao IS NULL
              AND i.codigo IS NOT NULL AND TRIM(i.codigo) != ''
              AND NOT EXISTS (
                  SELECT 1 FROM ferramentaria_ferramentas f
                  WHERE f.codigo = i.codigo AND f.ativo = 1
              )
            GROUP BY i.codigo
        )
    ''').fetchone()

    _valor_aberto = conn.execute('''
        SELECT COALESCE(SUM(i.quantidade * i.valor_unitario), 0) as valor
        FROM ferramentaria_retirada_itens i
        JOIN ferramentaria_retiradas r ON r.id = i.retirada_id
        WHERE r.data_devolucao IS NULL
    ''').fetchone()

    # Movimentação das últimas 8 semanas: retiradas x devoluções. Responde
    # "o giro está aumentando ou caindo", que nenhum gráfico atual mostrava.
    ferr_movimentacao = []
    _base_sem = datetime.now() - timedelta(days=datetime.now().weekday())
    for _i in range(7, -1, -1):
        _ini = (_base_sem - timedelta(weeks=_i)).strftime('%Y-%m-%d')
        _fim = (_base_sem - timedelta(weeks=_i) + timedelta(days=6)).strftime('%Y-%m-%d')
        _ret = conn.execute(
            'SELECT COUNT(*) as n FROM ferramentaria_retiradas WHERE data_retirada BETWEEN ? AND ?',
            (_ini, _fim)).fetchone()['n']
        _dev = conn.execute(
            'SELECT COUNT(*) as n FROM ferramentaria_retiradas WHERE data_devolucao BETWEEN ? AND ?',
            (_ini, _fim)).fetchone()['n']
        ferr_movimentacao.append({
            'semana': f"{_ini[8:10]}/{_ini[5:7]}",
            'retiradas': _ret, 'devolucoes': _dev
        })

    # Situação do estoque. Só entram itens que TÊM estoque cadastrado: a base tem
    # centenas de milhares de códigos de catálogo com quantidade zero, e incluí-los
    # afogaria o gráfico num "tudo zerado" que não diz nada. O tamanho do catálogo
    # sem estoque vira um indicador à parte, que é a informação realmente acionável.
    _sit = conn.execute('''
        SELECT
            SUM(CASE WHEN disp <= 0                     THEN 1 ELSE 0 END) as sem_saldo,
            SUM(CASE WHEN disp > 0 AND disp <= est_min  THEN 1 ELSE 0 END) as no_limite,
            SUM(CASE WHEN disp > est_min                THEN 1 ELSE 0 END) as folgado
        FROM (
            SELECT f.codigo,
                   IFNULL(f.estoque_total, 0) - IFNULL(e.emprestadas, 0) as disp,
                   IFNULL(f.estoque_minimo, 0)                           as est_min
            FROM ferramentaria_ferramentas f
            LEFT JOIN (
                SELECT i.codigo, SUM(i.quantidade) as emprestadas
                FROM ferramentaria_retirada_itens i
                JOIN ferramentaria_retiradas r ON r.id = i.retirada_id
                WHERE r.data_devolucao IS NULL
                  AND i.codigo IS NOT NULL AND TRIM(i.codigo) != ''
                GROUP BY i.codigo
            ) e ON e.codigo = f.codigo
            WHERE f.ativo = 1 AND IFNULL(f.estoque_total, 0) > 0
        )
    ''').fetchone()
    _catalogo = conn.execute(
        'SELECT COUNT(*) as n FROM ferramentaria_ferramentas WHERE ativo = 1'
    ).fetchone()['n']
    _com_estoque = conn.execute(
        'SELECT COUNT(*) as n FROM ferramentaria_ferramentas WHERE ativo = 1 AND IFNULL(estoque_total,0) > 0'
    ).fetchone()['n']
    ferr_situacao_estoque = {
        'sem_saldo': int(_sit['sem_saldo'] or 0),
        'no_limite': int(_sit['no_limite'] or 0),
        'folgado': int(_sit['folgado'] or 0),
        'catalogo': _catalogo,
        'com_estoque': _com_estoque,
        'sem_cadastro_estoque': max(_catalogo - _com_estoque, 0),
    }

    _tot_ret = int(_ind['total_retiradas'] or 0)
    ferr_indicadores = {
        'total_retiradas': _tot_ret,
        'em_aberto': int(_ind['em_aberto'] or 0),
        'devolvidas': int(_ind['devolvidas'] or 0),
        'taxa_devolucao_geral': round((int(_ind['devolvidas'] or 0) / _tot_ret) * 100, 1) if _tot_ret else 0,
        'colaboradores_com_posse': int(_ind['colaboradores_com_posse'] or 0),
        'maior_posse_dias': int(_ind['maior_posse_dias'] or 0),
        'itens_sem_cadastro': int(_criticos['qtd'] or 0),
        'valor_em_aberto': float(_valor_aberto['valor'] or 0),
    }

    # Taxa de devolução por mecânico (top 15 com mais retiradas)
    taxa_devolucao = [dict(r) for r in conn.execute('''
        SELECT
            mecanico,
            COUNT(*) as total_retiradas,
            SUM(CASE WHEN data_devolucao IS NOT NULL THEN 1 ELSE 0 END) as devolvidas,
            SUM(CASE WHEN data_devolucao IS NULL THEN 1 ELSE 0 END) as pendentes,
            ROUND(
                100.0 * SUM(CASE WHEN data_devolucao IS NOT NULL THEN 1 ELSE 0 END) / COUNT(*),
                1
            ) as taxa_pct
        FROM ferramentaria_retiradas
        GROUP BY mecanico
        HAVING total_retiradas > 0
        ORDER BY pendentes DESC, total_retiradas DESC
        LIMIT 15
    ''').fetchall()]

    # Valor total emprestado por mecânico (somente retiradas ainda em aberto)
    valor_emprestado = [dict(r) for r in conn.execute('''
        SELECT
            r.mecanico,
            COUNT(DISTINCT r.id) as num_retiradas,
            SUM(i.quantidade * i.valor_unitario) as valor_total
        FROM ferramentaria_retiradas r
        JOIN ferramentaria_retirada_itens i ON i.retirada_id = r.id
        WHERE r.data_devolucao IS NULL
          AND i.valor_unitario > 0
        GROUP BY r.mecanico
        ORDER BY valor_total DESC
        LIMIT 15
    ''').fetchall()]

    # Agregados (peças em fornecedores)
    agregados_modelo = request.args.get('modelo_agregados', 'CH570').strip().upper() or 'CH570'
    modelos_agregados = [r[0] for r in conn.execute(
        'SELECT DISTINCT modelo_maquina FROM ferramentaria_agregados WHERE ativo=1 ORDER BY modelo_maquina'
    ).fetchall()]
    agregados_rows = conn.execute('''
        SELECT a.id, a.item, a.categoria, a.codigo_novo, a.codigo_recond, a.descricao, a.referencia, a.versao,
               a.saldo_novo, a.p_conserto, a.saldo_recond, a.em_manut, a.devendo, a.imagem,
               COUNT(r.id) as num_registros,
               SUM(CASE WHEN r.status='EM CONSERTO'    THEN 1 ELSE 0 END) as num_conserto,
               SUM(CASE WHEN r.status='EM MANUTENÇÃO'  THEN 1 ELSE 0 END) as num_manut,
               SUM(CASE WHEN r.status='DEVENDO'         THEN 1 ELSE 0 END) as num_devendo,
               SUM(CASE WHEN r.status='APLICADA'        THEN 1 ELSE 0 END) as num_aplicada,
               SUM(CASE WHEN r.status='EM ESTOQUE' OR r.status IS NULL THEN 1 ELSE 0 END) as num_estoque
        FROM ferramentaria_agregados a
        LEFT JOIN agregados_registros r ON r.agregado_id = a.id
        WHERE a.ativo=1 AND a.modelo_maquina=?
        GROUP BY a.id
        ORDER BY a.id
    ''', (agregados_modelo,)).fetchall()
    # Agrupar por categoria
    from collections import OrderedDict as _OD
    agregados_por_categoria = _OD()
    for r in agregados_rows:
        cat = r['categoria'] or 'SEM CATEGORIA'
        if cat not in agregados_por_categoria:
            agregados_por_categoria[cat] = []
        agregados_por_categoria[cat].append(dict(r))
    agregados_totais = {
        'total': len(agregados_rows),
        'p_conserto': sum(r['num_conserto'] or 0 for r in agregados_rows),
        'em_manut': sum(r['num_manut'] or 0 for r in agregados_rows),
        'devendo': sum(r['num_devendo'] or 0 for r in agregados_rows),
        'aplicada': sum(r['num_aplicada'] or 0 for r in agregados_rows),
        'em_estoque': sum(r['num_estoque'] or 0 for r in agregados_rows),
    }

    # Caixas de ferramentas por mecânico
    caixas = [dict(r) for r in conn.execute('''
        SELECT c.id, c.mecanico, c.observacao, c.termo_pdf, c.atualizado_em,
               COUNT(i.id) as total_itens,
               COALESCE(SUM(i.quantidade), 0) as total_qty,
               COALESCE(SUM(i.quantidade * i.valor_unitario), 0) as valor_total
        FROM caixa_ferramentas c
        LEFT JOIN caixa_ferramentas_itens i ON i.caixa_id = c.id
        GROUP BY c.id
        ORDER BY c.mecanico
    ''').fetchall()]

    # Caminhões oficina
    caminhoes = [dict(r) for r in conn.execute('''
        SELECT c.id, c.identificacao, c.responsavel, c.observacao, c.termo_pdf, c.atualizado_em,
               COUNT(i.id) as total_itens,
               COALESCE(SUM(i.quantidade), 0) as total_qty,
               COALESCE(SUM(i.quantidade * i.valor_unitario), 0) as valor_total
        FROM caminhao_oficina c
        LEFT JOIN caminhao_oficina_itens i ON i.caminhao_id = c.id
        GROUP BY c.id
        ORDER BY c.identificacao
    ''').fetchall()]

    # Conferência: ferramentas faltando na ÚLTIMA conferência registrada de cada caminhão
    faltando_raw = conn.execute('''
        SELECT co.id as conferencia_id, co.caminhao_id, co.data_conferencia,
               c.identificacao, c.responsavel,
               ci.codigo, ci.descricao, ci.quantidade_esperada, ci.observacao
        FROM caminhao_conferencias co
        JOIN caminhao_oficina c ON c.id = co.caminhao_id
        JOIN caminhao_conferencia_itens ci ON ci.conferencia_id = co.id
        WHERE co.id IN (SELECT MAX(id) FROM caminhao_conferencias GROUP BY caminhao_id)
          AND ci.presente = 0
        ORDER BY c.identificacao, ci.descricao
    ''').fetchall()
    conferencia_faltando_por_caminhao = _OD()
    for r in faltando_raw:
        chave = r['caminhao_id']
        if chave not in conferencia_faltando_por_caminhao:
            conferencia_faltando_por_caminhao[chave] = {
                'identificacao': r['identificacao'],
                'responsavel': r['responsavel'],
                'data_conferencia': r['data_conferencia'],
                'itens': []
            }
        conferencia_faltando_por_caminhao[chave]['itens'].append(dict(r))

    # Histórico de conferências (mais recentes primeiro)
    conferencias_historico = [dict(r) for r in conn.execute('''
        SELECT co.id, co.caminhao_id, co.data_conferencia, co.usuario, co.observacao,
               co.total_itens, co.total_faltando, c.identificacao
        FROM caminhao_conferencias co
        JOIN caminhao_oficina c ON c.id = co.caminhao_id
        ORDER BY co.data_conferencia DESC, co.id DESC
        LIMIT 100
    ''').fetchall()]

    conn.close()
    return render_template(
        'ferramentaria.html',
        historico=historico,
        ferramentas=ferramentas_lista,
        estoque_lista=[dict(e) for e in estoque_lista],
        estoque_resumo={
            'total_estoque': total_estoque_calc,
            'total_emprestado': total_emprestado_calc,
            'total_disponivel': total_disponivel_calc,
            'percentual_disponivel': percentual_disponivel
        },
        top_colaboradores=top_colaboradores,
        top_tempo=top_tempo,
        top_ferramentas=top_ferramentas,
        ferr_indicadores=ferr_indicadores,
        ferr_movimentacao=ferr_movimentacao,
        ferr_situacao_estoque=ferr_situacao_estoque,
        taxa_devolucao=taxa_devolucao,
        valor_emprestado=valor_emprestado,
        total_ferramentas=total_ferramentas,
        total_ferramentas_filtrado=total_ferramentas_filtrado,
        pagina_ferramentas=pagina_ferramentas,
        total_paginas_ferramentas=total_paginas_ferramentas,
        busca_ferramenta=busca_ferramenta,
        active_tab=active_tab,
        hoje=datetime.now().strftime('%Y-%m-%d'),
        agregados_por_categoria=agregados_por_categoria,
        agregados_totais=agregados_totais,
        modelos_agregados=modelos_agregados,
        agregados_modelo=agregados_modelo,
        caixas=caixas,
        caminhoes=caminhoes,
        conferencia_faltando_por_caminhao=conferencia_faltando_por_caminhao,
        conferencias_historico=conferencias_historico,
        hist_mecanico=hist_mecanico,
        hist_frota=hist_frota,
        hist_ferramenta=hist_ferramenta,
        hist_status=hist_status,
        hist_data_inicio=hist_data_inicio,
        hist_data_fim=hist_data_fim,
        hist_tem_filtro=hist_tem_filtro,
        total_historico_geral=total_historico_geral,
    )



@app.route('/ferramentaria/relatorio_pdf')
def ferramentaria_relatorio_pdf():
    if not session.get('logado'): return redirect(url_for('login'))
    if not tem_acesso_modulo('ferramentaria'):
        flash('Sem permissao para acessar Ferramentaria.', 'danger')
        return redirect(url_for('admin'))

    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_LEFT, TA_RIGHT
    import io

    periodo = request.args.get('periodo', 'semana').strip().lower()
    hoje = datetime.now().date()
    meses_pt = ['Janeiro','Fevereiro','Marco','Abril','Maio','Junho',
                'Julho','Agosto','Setembro','Outubro','Novembro','Dezembro']

    if periodo == 'dia':
        data_inicio = hoje
        data_fim    = hoje
        label_periodo = hoje.strftime('%d/%m/%Y')
        nome_periodo  = 'Hoje'
        nome_arquivo  = hoje.strftime('%Y%m%d')
    elif periodo == 'mes':
        data_inicio = hoje.replace(day=1)
        data_fim    = hoje
        label_periodo = meses_pt[hoje.month - 1] + ' / ' + str(hoje.year)
        nome_periodo  = 'Este Mes'
        nome_arquivo  = hoje.strftime('%Y%m')
    else:
        dom = hoje - timedelta(days=hoje.weekday() + 1) if hoje.weekday() != 6 else hoje
        sab = dom + timedelta(days=6)
        data_inicio = dom
        data_fim    = sab
        label_periodo = dom.strftime('%d/%m/%Y') + ' a ' + sab.strftime('%d/%m/%Y')
        nome_periodo  = 'Esta Semana'
        nome_arquivo  = dom.strftime('%Y%m%d') + '_' + sab.strftime('%Y%m%d')

    d_ini = data_inicio.strftime('%Y-%m-%d')
    d_fim = data_fim.strftime('%Y-%m-%d')

    conn = get_db_connection()

    estoque_resumo = conn.execute(
        "SELECT COALESCE(SUM(f.estoque_total + COALESCE(e.emprestadas, 0)), 0) as total_estoque,"
        " COALESCE(SUM(COALESCE(e.emprestadas, 0)), 0) as total_emprestado,"
        " COALESCE(SUM(f.estoque_total), 0) as total_disponivel"
        " FROM ferramentaria_ferramentas f"
        " LEFT JOIN ("
        "   SELECT i.codigo, SUM(i.quantidade) as emprestadas"
        "   FROM ferramentaria_retirada_itens i"
        "   JOIN ferramentaria_retiradas r ON r.id = i.retirada_id"
        "   WHERE r.data_devolucao IS NULL AND i.codigo IS NOT NULL AND TRIM(i.codigo) != ''"
        "   GROUP BY i.codigo"
        " ) e ON e.codigo = f.codigo"
        " WHERE f.ativo = 1"
    ).fetchone()

    taxa_devolucao = conn.execute(
        "SELECT mecanico,"
        " COUNT(*) as total_retiradas,"
        " SUM(CASE WHEN data_devolucao IS NOT NULL THEN 1 ELSE 0 END) as devolvidas,"
        " SUM(CASE WHEN data_devolucao IS NULL THEN 1 ELSE 0 END) as pendentes,"
        " ROUND(100.0 * SUM(CASE WHEN data_devolucao IS NOT NULL THEN 1 ELSE 0 END) / COUNT(*), 1) as taxa_pct"
        " FROM ferramentaria_retiradas"
        " WHERE data_retirada >= ? AND data_retirada <= ?"
        " GROUP BY mecanico HAVING total_retiradas > 0"
        " ORDER BY pendentes DESC, total_retiradas DESC LIMIT 20",
        (d_ini, d_fim)
    ).fetchall()

    valor_emprestado = conn.execute(
        "SELECT r.mecanico, COUNT(DISTINCT r.id) as num_retiradas,"
        " SUM(i.quantidade * i.valor_unitario) as valor_total"
        " FROM ferramentaria_retiradas r"
        " JOIN ferramentaria_retirada_itens i ON i.retirada_id = r.id"
        " WHERE r.data_devolucao IS NULL AND i.valor_unitario > 0"
        " AND r.data_retirada >= ? AND r.data_retirada <= ?"
        " GROUP BY r.mecanico ORDER BY valor_total DESC LIMIT 20",
        (d_ini, d_fim)
    ).fetchall()

    top_colaboradores = conn.execute(
        "SELECT r.mecanico, SUM(i.quantidade) as total,"
        " CAST(julianday('now') - julianday(MIN(r.data_retirada)) AS INTEGER) as dias"
        " FROM ferramentaria_retiradas r"
        " JOIN ferramentaria_retirada_itens i ON i.retirada_id = r.id"
        " WHERE r.data_devolucao IS NULL AND r.data_retirada >= ? AND r.data_retirada <= ?"
        " GROUP BY r.mecanico ORDER BY total DESC LIMIT 15",
        (d_ini, d_fim)
    ).fetchall()

    top_ferramentas = conn.execute(
        "SELECT COALESCE(NULLIF(TRIM(codigo), ''), '-') as codigo, descricao, SUM(quantidade) as total"
        " FROM ferramentaria_retirada_itens i"
        " JOIN ferramentaria_retiradas r ON r.id = i.retirada_id"
        " WHERE r.data_devolucao IS NULL AND r.data_retirada >= ? AND r.data_retirada <= ?"
        " GROUP BY COALESCE(NULLIF(TRIM(codigo), ''), descricao), descricao"
        " ORDER BY total DESC LIMIT 10",
        (d_ini, d_fim)
    ).fetchall()

    conn.close()

    total_e    = float(estoque_resumo['total_estoque']   or 0)
    total_emp  = float(estoque_resumo['total_emprestado'] or 0)
    total_disp = float(estoque_resumo['total_disponivel'] or 0)
    pct_disp   = round((total_disp / total_e * 100), 1) if total_e > 0 else 0

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=1.5*cm, rightMargin=1.5*cm,
                            topMargin=1.5*cm, bottomMargin=1.5*cm)

    azul        = colors.HexColor('#003d80')
    cinza_esc   = colors.HexColor('#343a40')
    cinza_claro = colors.HexColor('#f2f4f7')
    verde       = colors.HexColor('#198754')
    vermelho    = colors.HexColor('#dc3545')

    titulo_s = ParagraphStyle('tit_f2', fontName='Helvetica-Bold', fontSize=16, textColor=azul, spaceAfter=2)
    sub_s    = ParagraphStyle('sub_f2', fontName='Helvetica',      fontSize=9,  textColor=colors.HexColor('#6c757d'), spaceAfter=2)
    per_s    = ParagraphStyle('per_f2', fontName='Helvetica-Bold', fontSize=10, textColor=colors.HexColor('#495057'), spaceAfter=8)
    sec_s    = ParagraphStyle('sec_f2', fontName='Helvetica-Bold', fontSize=11, textColor=azul, spaceBefore=14, spaceAfter=6)
    rod_s    = ParagraphStyle('rod_f2', fontName='Helvetica',      fontSize=7,  textColor=colors.HexColor('#6c757d'), alignment=2)
    styles   = getSampleStyleSheet()

    def tab_style(hc=None):
        hc = hc or azul
        return TableStyle([
            ('BACKGROUND',    (0,0), (-1,0), hc),
            ('TEXTCOLOR',     (0,0), (-1,0), colors.white),
            ('FONTNAME',      (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE',      (0,0), (-1,0), 8),
            ('ALIGN',         (0,0), (-1,0), 'CENTER'),
            ('VALIGN',        (0,0), (-1,-1), 'MIDDLE'),
            ('FONTNAME',      (0,1), (-1,-1), 'Helvetica'),
            ('FONTSIZE',      (0,1), (-1,-1), 8),
            ('ROWBACKGROUNDS',(0,1), (-1,-1), [colors.white, cinza_claro]),
            ('GRID',          (0,0), (-1,-1), 0.3, colors.HexColor('#dee2e6')),
            ('LEFTPADDING',   (0,0), (-1,-1), 5),
            ('RIGHTPADDING',  (0,0), (-1,-1), 5),
            ('TOPPADDING',    (0,0), (-1,-1), 4),
            ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ])

    def fmt_brl(v):
        s = '{:,.2f}'.format(float(v))
        return 'R$ ' + s.replace(',', 'X').replace('.', ',').replace('X', '.')

    story = []
    data_geracao = datetime.now().strftime('%d/%m/%Y %H:%M')

    story.append(Paragraph('PCM - Relatorio Analitico de Ferramentaria', titulo_s))
    story.append(Paragraph('Gerado em ' + data_geracao, sub_s))
    story.append(Paragraph('Periodo: ' + nome_periodo + ' | ' + label_periodo, per_s))
    story.append(HRFlowable(width='100%', thickness=1.5, color=azul, spaceAfter=12))

    story.append(Paragraph('Resumo do Estoque (snapshot atual)', sec_s))
    t_res = Table(
        [['Estoque Total', 'Disponivel', 'Emprestadas', 'Taxa Disponivel'],
         [str(int(total_e)), str(int(total_disp)), str(int(total_emp)), str(pct_disp) + '%']],
        colWidths=[4*cm]*4)
    ts_res = tab_style(azul)
    ts_res.add('ALIGN',    (0,1), (-1,1), 'CENTER')
    ts_res.add('FONTNAME', (0,1), (-1,1), 'Helvetica-Bold')
    ts_res.add('FONTSIZE', (0,1), (-1,1), 14)
    ts_res.add('TEXTCOLOR',(1,1), (1,1), verde)
    ts_res.add('TEXTCOLOR',(2,1), (2,1), vermelho)
    ts_res.add('TEXTCOLOR',(3,1), (3,1), colors.HexColor('#0d6efd'))
    t_res.setStyle(ts_res)
    story.append(t_res)

    story.append(Paragraph('Taxa de Devolucao por Mecanico (' + nome_periodo + ')', sec_s))
    if taxa_devolucao:
        dev_rows = [['Mecanico', 'Total', 'Devolvidas', 'Pendentes', 'Taxa (%)']]
        for row in taxa_devolucao:
            dev_rows.append([str(row['mecanico'] or '-'), str(int(row['total_retiradas'] or 0)),
                str(int(row['devolvidas'] or 0)), str(int(row['pendentes'] or 0)),
                '{:.1f}%'.format(float(row['taxa_pct'] or 0))])
        t_dev = Table(dev_rows, colWidths=[7*cm, 2*cm, 2.5*cm, 2.5*cm, 2.5*cm])
        ts_dev = tab_style(verde)
        for i, row in enumerate(taxa_devolucao, start=1):
            if int(row['pendentes'] or 0) > 0:
                ts_dev.add('TEXTCOLOR', (3,i), (3,i), vermelho)
                ts_dev.add('FONTNAME',  (3,i), (3,i), 'Helvetica-Bold')
            taxa = float(row['taxa_pct'] or 0)
            if taxa < 50:
                ts_dev.add('TEXTCOLOR', (4,i), (4,i), vermelho)
            elif taxa >= 90:
                ts_dev.add('TEXTCOLOR', (4,i), (4,i), verde)
        t_dev.setStyle(ts_dev)
        story.append(t_dev)
    else:
        story.append(Paragraph('Nenhuma retirada no periodo (' + label_periodo + ').', styles['Normal']))

    story.append(Paragraph('Valor Total Emprestado em Aberto (' + nome_periodo + ')', sec_s))
    if valor_emprestado:
        val_rows = [['Mecanico', 'Retiradas Abertas', 'Valor Total']]
        total_geral = 0.0
        for row in valor_emprestado:
            v = float(row['valor_total'] or 0)
            total_geral += v
            val_rows.append([str(row['mecanico'] or '-'), str(int(row['num_retiradas'] or 0)), fmt_brl(v)])
        val_rows.append(['TOTAL GERAL', '', fmt_brl(total_geral)])
        t_val = Table(val_rows, colWidths=[8*cm, 4*cm, 4.5*cm])
        ts_val = tab_style(colors.HexColor('#856404'))
        n = len(val_rows) - 1
        ts_val.add('FONTNAME',   (0,n), (-1,n), 'Helvetica-Bold')
        ts_val.add('BACKGROUND', (0,n), (-1,n), colors.HexColor('#fff3cd'))
        ts_val.add('TEXTCOLOR',  (0,n), (-1,n), colors.HexColor('#856404'))
        ts_val.add('ALIGN',      (1,1), (-1,-1), 'RIGHT')
        t_val.setStyle(ts_val)
        story.append(t_val)
    else:
        story.append(Paragraph('Nenhum item com valor em aberto no periodo.', styles['Normal']))

    story.append(Paragraph('Colaboradores com Ferramentas em Maos (' + nome_periodo + ')', sec_s))
    if top_colaboradores:
        colab_rows = [['Mecanico', 'Qtd. Ferramentas', 'Dias em Posse']]
        for row in top_colaboradores:
            colab_rows.append([str(row['mecanico'] or '-'), str(int(row['total'] or 0)),
                str(int(row['dias'] or 0)) + ' dias'])
        t_colab = Table(colab_rows, colWidths=[9*cm, 4*cm, 3.5*cm])
        ts_colab = tab_style(cinza_esc)
        ts_colab.add('ALIGN', (1,1), (-1,-1), 'CENTER')
        t_colab.setStyle(ts_colab)
        story.append(t_colab)
    else:
        story.append(Paragraph('Nenhuma ferramenta em posse no periodo.', styles['Normal']))

    story.append(Paragraph('Ferramentas Mais Emprestadas (' + nome_periodo + ')', sec_s))
    if top_ferramentas:
        ferr_rows = [['Codigo', 'Descricao', 'Qtd.']]
        for row in top_ferramentas:
            ferr_rows.append([str(row['codigo'] or '-'), str(row['descricao'] or '-'),
                str(int(row['total'] or 0))])
        t_ferr = Table(ferr_rows, colWidths=[3*cm, 11*cm, 2.5*cm])
        ts_ferr = tab_style(colors.HexColor('#0d6efd'))
        ts_ferr.add('ALIGN', (2,1), (2,-1), 'CENTER')
        t_ferr.setStyle(ts_ferr)
        story.append(t_ferr)
    else:
        story.append(Paragraph('Nenhuma ferramenta emprestada no periodo.', styles['Normal']))

    story.append(Spacer(1, 16))
    story.append(HRFlowable(width='100%', thickness=0.5, color=colors.HexColor('#dee2e6')))
    story.append(Paragraph(
        'PCM - Sistema de Gestao | Ferramentaria | ' + nome_periodo + ': ' + label_periodo + ' | Gerado em ' + data_geracao,
        rod_s))

    doc.build(story)
    buf.seek(0)

    from flask import Response
    nome_arq = 'ferramentaria_' + periodo + '_' + nome_arquivo + '.pdf'
    return Response(buf.read(), mimetype='application/pdf',
        headers={'Content-Disposition': 'inline; filename=' + nome_arq})



@app.route('/ferramentaria/relatorio_grafico')
def ferramentaria_relatorio_grafico():
    if not session.get('logado'): return redirect(url_for('login'))
    if not tem_acesso_modulo('ferramentaria'):
        flash('Sem permissao para acessar Ferramentaria.', 'danger')
        return redirect(url_for('admin'))

    periodo = request.args.get('periodo', 'semana').strip().lower()
    hoje = datetime.now().date()
    meses_pt = ['Janeiro','Fevereiro','Marco','Abril','Maio','Junho',
                'Julho','Agosto','Setembro','Outubro','Novembro','Dezembro']

    if periodo == 'dia':
        data_inicio = hoje; data_fim = hoje
        label_periodo = hoje.strftime('%d/%m/%Y')
        nome_periodo  = 'Hoje'
        nome_arquivo  = hoje.strftime('%Y%m%d')
    elif periodo == 'mes':
        data_inicio = hoje.replace(day=1); data_fim = hoje
        label_periodo = meses_pt[hoje.month - 1] + ' / ' + str(hoje.year)
        nome_periodo  = 'Este Mes'
        nome_arquivo  = hoje.strftime('%Y%m')
    elif periodo == 'personalizado':
        from datetime import date as _date
        try:
            data_inicio = _date.fromisoformat(request.args.get('data_ini', ''))
            data_fim    = _date.fromisoformat(request.args.get('data_fim', ''))
            if data_fim < data_inicio:
                data_fim = data_inicio
        except ValueError:
            data_inicio = hoje; data_fim = hoje
        label_periodo = data_inicio.strftime('%d/%m/%Y') + ' a ' + data_fim.strftime('%d/%m/%Y')
        nome_periodo  = 'Personalizado'
        nome_arquivo  = data_inicio.strftime('%Y%m%d') + '_' + data_fim.strftime('%Y%m%d')
    else:
        dom = hoje - timedelta(days=hoje.weekday() + 1) if hoje.weekday() != 6 else hoje
        sab = dom + timedelta(days=6)
        data_inicio = dom; data_fim = sab
        label_periodo = dom.strftime('%d/%m/%Y') + ' a ' + sab.strftime('%d/%m/%Y')
        nome_periodo  = 'Esta Semana'
        nome_arquivo  = dom.strftime('%Y%m%d') + '_' + sab.strftime('%Y%m%d')

    d_ini = data_inicio.strftime('%Y-%m-%d')
    d_fim = data_fim.strftime('%Y-%m-%d')

    conn = get_db_connection()

    estoque_row = conn.execute(
        "SELECT COALESCE(SUM(f.estoque_total + COALESCE(e.emp,0)),0) as total_estoque,"
        " COALESCE(SUM(COALESCE(e.emp,0)),0) as total_emprestado,"
        " COALESCE(SUM(f.estoque_total),0) as total_disponivel,"
        " COUNT(f.id) as total_ferramentas"
        " FROM ferramentaria_ferramentas f"
        " LEFT JOIN (SELECT i.codigo, SUM(i.quantidade) as emp"
        "   FROM ferramentaria_retirada_itens i"
        "   JOIN ferramentaria_retiradas r ON r.id=i.retirada_id"
        "   WHERE r.data_devolucao IS NULL AND i.codigo IS NOT NULL AND TRIM(i.codigo)!=''"
        "   GROUP BY i.codigo) e ON e.codigo=f.codigo"
        " WHERE f.ativo=1"
    ).fetchone()

    te = float(estoque_row['total_estoque'] or 0)
    emp= float(estoque_row['total_emprestado'] or 0)
    td = float(estoque_row['total_disponivel'] or 0)
    pct= round((td/te*100),1) if te>0 else 0
    estoque = {'total_estoque': te, 'total_emprestado': emp, 'total_disponivel': td, 'percentual_disponivel': pct}
    total_ferramentas = int(estoque_row['total_ferramentas'] or 0)

    taxa_devolucao = [dict(r) for r in conn.execute(
        "SELECT mecanico, COUNT(*) as total_retiradas,"
        " SUM(CASE WHEN data_devolucao IS NOT NULL THEN 1 ELSE 0 END) as devolvidas,"
        " SUM(CASE WHEN data_devolucao IS NULL THEN 1 ELSE 0 END) as pendentes,"
        " ROUND(100.0*SUM(CASE WHEN data_devolucao IS NOT NULL THEN 1 ELSE 0 END)/COUNT(*),1) as taxa_pct"
        " FROM ferramentaria_retiradas"
        " WHERE data_retirada>=? AND data_retirada<=?"
        " GROUP BY mecanico HAVING total_retiradas>0"
        " ORDER BY pendentes DESC, total_retiradas DESC LIMIT 15",
        (d_ini, d_fim)
    ).fetchall()]

    valor_emprestado = [dict(r) for r in conn.execute(
        "SELECT r.mecanico, COUNT(DISTINCT r.id) as num_retiradas,"
        " SUM(i.quantidade*i.valor_unitario) as valor_total"
        " FROM ferramentaria_retiradas r"
        " JOIN ferramentaria_retirada_itens i ON i.retirada_id=r.id"
        " WHERE r.data_devolucao IS NULL AND i.valor_unitario>0"
        " AND r.data_retirada>=? AND r.data_retirada<=?"
        " GROUP BY r.mecanico ORDER BY valor_total DESC LIMIT 10",
        (d_ini, d_fim)
    ).fetchall()]

    top_colaboradores = [dict(r) for r in conn.execute(
        "SELECT r.mecanico, SUM(i.quantidade) as total,"
        " CAST(julianday('now')-julianday(MIN(r.data_retirada)) AS INTEGER) as dias"
        " FROM ferramentaria_retiradas r"
        " JOIN ferramentaria_retirada_itens i ON i.retirada_id=r.id"
        " WHERE r.data_devolucao IS NULL AND r.data_retirada>=? AND r.data_retirada<=?"
        " GROUP BY r.mecanico ORDER BY total DESC LIMIT 10",
        (d_ini, d_fim)
    ).fetchall()]

    top_ferramentas = [dict(r) for r in conn.execute(
        "SELECT COALESCE(NULLIF(TRIM(codigo),''),'-') as codigo, descricao, SUM(quantidade) as total"
        " FROM ferramentaria_retirada_itens i"
        " JOIN ferramentaria_retiradas r ON r.id=i.retirada_id"
        " WHERE r.data_devolucao IS NULL AND r.data_retirada>=? AND r.data_retirada<=?"
        " GROUP BY COALESCE(NULLIF(TRIM(codigo),''),descricao), descricao"
        " ORDER BY total DESC LIMIT 10",
        (d_ini, d_fim)
    ).fetchall()]

    num_mecanicos = len(top_colaboradores)
    valor_total = sum(float(r['valor_total'] or 0) for r in valor_emprestado)
    vt = '{:,.2f}'.format(valor_total)
    valor_total_fmt = 'R$ ' + vt.replace(',','X').replace('.',',').replace('X','.')

    total_ret_geral = sum(int(r['total_retiradas'] or 0) for r in taxa_devolucao)
    total_dev_geral = sum(int(r['devolvidas'] or 0) for r in taxa_devolucao)
    pct_devolvidas = round((total_dev_geral / total_ret_geral * 100), 1) if total_ret_geral > 0 else 0

    conn.close()

    return render_template('relatorio_ferramentaria.html',
        periodo=periodo, nome_periodo=nome_periodo,
        label_periodo=label_periodo, nome_arquivo=nome_arquivo,
        data_geracao=datetime.now().strftime('%d/%m/%Y %H:%M'),
        estoque=estoque, total_ferramentas=total_ferramentas,
        num_mecanicos=num_mecanicos, valor_total_fmt=valor_total_fmt,
        taxa_devolucao=taxa_devolucao, valor_emprestado=valor_emprestado,
        top_colaboradores=top_colaboradores, top_ferramentas=top_ferramentas,
        pct_devolvidas=pct_devolvidas)



def _periodo_relatorio_prev():
    """Início/fim do relatório: por padrão o mês corrente, como na planilha."""
    ini = (request.args.get('inicio') or '').strip()
    fim = (request.args.get('fim') or '').strip()
    if ini and fim:
        return ini, fim
    hoje = datetime.now()
    primeiro = hoje.replace(day=1)
    if primeiro.month == 12:
        prox = primeiro.replace(year=primeiro.year + 1, month=1)
    else:
        prox = primeiro.replace(month=primeiro.month + 1)
    ultimo = prox - timedelta(days=1)
    return primeiro.strftime('%Y-%m-%d'), ultimo.strftime('%Y-%m-%d')


LIMIAR_GRUPO_PROPRIO = 20   # frotas cadastradas para um subtipo virar relatório próprio


def _mapa_agrupamento(conn, agrupar):
    """Diz em que grupo cada frota cai: por setor (cadastro) ou por frente.

    A frente não fica no cadastro de frotas - é o vínculo cod_frota → frente
    mantido em `frentes_config`, pela tela de Frentes de Serviço. Quem não tem
    vínculo entra em SEM FRENTE, e não some do relatório.
    """
    if agrupar == 'frente':
        linhas = conn.execute('SELECT cod_frota, frente FROM frentes_config').fetchall()
        mapa = {r['cod_frota']: (r['frente'] or '').strip() or 'SEM FRENTE' for r in linhas}
        return mapa, 'SEM FRENTE'

    linhas = conn.execute('SELECT id_frota, setor FROM frotas').fetchall()
    mapa = {r['id_frota']: (r['setor'] or '').strip() or 'SEM SETOR' for r in linhas}
    return mapa, 'SEM SETOR'


# Faixas de turno da planilha de programação.
TURNOS_REPORT = [('A', '07:00 15:20'), ('B', '15:20 23:00'), ('C', '23:20 07:20')]


def _mapa_conjuntos(conn):
    """Diz a que conjunto cada frota pertence (cavalo + reboques, por exemplo).

    Duas fontes, nesta ordem: a coluna `frotas.conjunto` e a tabela
    `grupo_frotas`, que é o cadastro de grupos da tela de frotas. Frota sem
    conjunto devolve vazio e aparece sozinha na célula.
    """
    mapa = {}
    try:
        for r in conn.execute("SELECT id_frota, conjunto FROM frotas WHERE TRIM(COALESCE(conjunto,'')) <> ''"):
            mapa[r['id_frota']] = r['conjunto'].strip()
    except sqlite3.OperationalError:
        pass
    try:
        for r in conn.execute('SELECT grupo_id, id_frota FROM grupo_frotas'):
            mapa.setdefault(r['id_frota'], 'G%s' % r['grupo_id'])
    except sqlite3.OperationalError:
        pass
    return mapa


def _mapa_grupos_especialidade(conn):
    """Agrupa as especialidades em famílias, uma por relatório.

    A especialidade é gravada como "FAMILIA - SUBTIPO". Um subtipo com frota
    grande merece o seu próprio documento (CAMINHAO - CANAVIEIRO, TRATOR -
    TRANSBORDO, COLHEDORA - CANA); os pequenos da mesma família caem em
    "FAMILIA - DIVERSOS", senão sairiam dezenas de PDFs com duas frotas cada.
    """
    linhas = conn.execute('''
        SELECT COALESCE(NULLIF(TRIM(especialidade), ''), 'SEM ESPECIALIDADE') as esp,
               COUNT(*) as n
        FROM frotas GROUP BY esp
    ''').fetchall()

    mapa = {}
    for r in linhas:
        esp = r['esp']
        familia, _, sub = esp.partition(' - ')
        familia = familia.strip() or 'SEM ESPECIALIDADE'
        sub = sub.strip()
        if not sub:
            mapa[esp] = familia
        elif r['n'] >= LIMIAR_GRUPO_PROPRIO:
            mapa[esp] = '%s - %s' % (familia, sub)
        else:
            mapa[esp] = '%s - DIVERSOS' % familia
    return mapa


@app.route('/relatorios_preventiva')
def relatorios_preventiva():
    """Lista os setores com programação no período - cada setor gera o seu
    próprio relatório, em vez de um documento único com tudo misturado."""
    if not session.get('logado'):
        return redirect(url_for('login'))
    if not tem_acesso_modulo('programacao'):
        flash('Sem permissão para acessar os relatórios de programação.', 'danger')
        return redirect(url_for('admin'))

    ini, fim = _periodo_relatorio_prev()
    tipo = (request.args.get('tipo') or 'PREVENTIVA').strip().upper()

    conn = get_db_connection()
    # O relatório é sempre por setor; a frente é uma divisão dentro dele.
    mapa_grupo, sem_grupo = _mapa_agrupamento(conn, 'setor')
    total_vinculos = len(_mapa_agrupamento(conn, 'frente')[0])
    linhas = conn.execute('''
        SELECT p.id_frota,
               COALESCE(NULLIF(TRIM(p.status), ''), 'PENDENTE') as status,
               COALESCE(NULLIF(TRIM(f.especialidade), ''), 'SEM ESPECIALIDADE') as especialidade
        FROM programacao_semanal p
        LEFT JOIN frotas f ON f.id_frota = p.id_frota
        WHERE p.data_planejada BETWEEN ? AND ? AND p.tipo_servico LIKE ?
    ''', (ini, fim, f'%{tipo}%')).fetchall()
    conn.close()

    acumulado = {}
    for r in linhas:
        grupo = mapa_grupo.get(r['id_frota'], sem_grupo)
        acc = acumulado.setdefault(grupo, {'servicos': 0, 'realizadas': 0, 'quebradas': 0,
                                           'frotas': set(), 'especialidades': set()})
        acc['servicos'] += 1
        acc['frotas'].add(r['id_frota'])
        acc['especialidades'].add(r['especialidade'])
        if r['status'] == 'REALIZADA':
            acc['realizadas'] += 1
        elif r['status'] == 'QUEBRADA':
            acc['quebradas'] += 1

    setores = []
    for nome, a in acumulado.items():
        valido = a['servicos'] - a['quebradas']
        setores.append({
            'nome': nome,
            'frotas': len(a['frotas']),
            'especialidades': len(a['especialidades']),
            'servicos': a['servicos'],
            'realizadas': a['realizadas'],
            'aderencia': round((a['realizadas'] / valido) * 100, 1) if valido > 0 else 0,
        })
    setores.sort(key=lambda s: (-s['frotas'], s['nome']))

    return render_template('relatorios_preventiva.html',
                           setores=setores, inicio=ini, fim=fim, tipo=tipo,
                           total_vinculos=total_vinculos)


@app.route('/relatorio_preventiva_setor')
@app.route('/relatorio_preventiva_especialidade')   # caminho antigo, mantido vivo
def relatorio_preventiva_setor():
    """Relatório de um setor, no formato da planilha: grade de dias por
    especialidade, indicadores no topo e gráficos no rodapé."""
    if not session.get('logado'):
        return redirect(url_for('login'))
    if not tem_acesso_modulo('programacao'):
        flash('Sem permissão para acessar os relatórios de programação.', 'danger')
        return redirect(url_for('admin'))

    setor = (request.args.get('setor') or '').strip()
    tipo = (request.args.get('tipo') or 'PREVENTIVA').strip().upper()
    ini, fim = _periodo_relatorio_prev()
    d_ini = datetime.strptime(ini, '%Y-%m-%d')
    d_fim = datetime.strptime(fim, '%Y-%m-%d')

    conn = get_db_connection()
    grupos_esp = _mapa_grupos_especialidade(conn)
    # O relatório é de um setor; dentro dele as frotas se separam por frente.
    mapa_grupo, sem_grupo = _mapa_agrupamento(conn, 'setor')
    mapa_sub, sem_sub = _mapa_agrupamento(conn, 'frente')
    conjuntos = _mapa_conjuntos(conn)
    registros = conn.execute('''
        SELECT p.id_frota, p.tipo_servico, p.data_planejada, p.data_execucao,
               COALESCE(NULLIF(TRIM(p.status), ''), 'PENDENTE') as status,
               COALESCE(NULLIF(TRIM(f.descricao), ''), '')      as descricao,
               COALESCE(NULLIF(TRIM(f.turno), ''), '')          as turno,
               COALESCE(NULLIF(TRIM(f.especialidade), ''), 'SEM ESPECIALIDADE') as especialidade
        FROM programacao_semanal p
        LEFT JOIN frotas f ON f.id_frota = p.id_frota
        WHERE p.data_planejada BETWEEN ? AND ? AND p.tipo_servico LIKE ?
        ORDER BY p.id_frota, p.data_planejada
    ''', (ini, fim, f'%{tipo}%')).fetchall()
    conn.close()

    registros = [dict(r) for r in registros]
    for r in registros:
        # As linhas da grade são especialidades; os subtipos pequenos entram
        # agrupados para o setor não virar uma lista de linhas de uma frota.
        r['grupo'] = grupos_esp.get(r['especialidade'], r['especialidade'])
        r['agrupamento'] = mapa_grupo.get(r['id_frota'], sem_grupo)
        r['sub'] = mapa_sub.get(r['id_frota'], sem_sub)
        r['conjunto'] = conjuntos.get(r['id_frota'], '')
    if setor:
        registros = [r for r in registros if r['agrupamento'] == setor]

    # Dias do período, como colunas da grade.
    dias = []
    cursor_dia = d_ini
    nomes_dia = ['SEGUNDA', 'TERÇA', 'QUARTA', 'QUINTA', 'SEXTA', 'SÁBADO', 'DOMINGO']
    meses_abrev = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez']
    while cursor_dia <= d_fim:
        dias.append({
            'db': cursor_dia.strftime('%Y-%m-%d'),
            'rotulo': f"{cursor_dia.day:02d}/{meses_abrev[cursor_dia.month - 1]}",
            'dia_semana': nomes_dia[cursor_dia.weekday()],
            'fim_semana': cursor_dia.weekday() >= 5,
            'semana': cursor_dia.isocalendar()[1],
        })
        cursor_dia += timedelta(days=1)

    def classe_status(reg):
        st = reg['status']
        if st == 'REALIZADA':
            atrasou = bool(reg['data_execucao'] and reg['data_execucao'] > reg['data_planejada'])
            return 'atraso' if atrasou else 'ok'
        if st == 'BLOQUEADA':
            return 'manutencao'
        if st == 'QUEBRADA':
            return 'parado'
        return 'pendente'

    # Grade da planilha: bloco (especialidade) → sub-bloco (frente/setor) →
    # faixa de turno → dia → conjuntos. Frotas do mesmo conjunto ficam coladas
    # na mesma célula, porque rodam e são programadas juntas.
    def ordem_turno(t):
        for i, (letra, _) in enumerate(TURNOS_REPORT):
            if t == letra:
                return i
        return len(TURNOS_REPORT)

    horario_turno = dict(TURNOS_REPORT)
    blocos_map = {}
    for r in registros:
        bloco = blocos_map.setdefault(r['grupo'], {'nome': r['grupo'], 'subs': {}, 'frotas': set()})
        bloco['frotas'].add(r['id_frota'])
        sub = bloco['subs'].setdefault(r['sub'], {'nome': r['sub'], 'turnos': {}, 'frotas': set()})
        sub['frotas'].add(r['id_frota'])
        faixa = sub['turnos'].setdefault(r['turno'], {'turno': r['turno'],
                                                      'horario': horario_turno.get(r['turno'], ''),
                                                      'dias': {}, 'frotas': set()})
        faixa['frotas'].add(r['id_frota'])
        # Sem conjunto cadastrado, a própria frota é o seu conjunto.
        chave = r['conjunto'] or ('#' + r['id_frota'])
        celulas = faixa['dias'].setdefault(r['data_planejada'], {})
        celulas.setdefault(chave, []).append({
            'frota': r['id_frota'], 'descricao': r['descricao'], 'classe': classe_status(r)
        })

    def medir(regs):
        tot = real = queb = 0
        for c in regs:
            tot += 1
            if c in ('ok', 'atraso'):
                real += 1
            elif c == 'parado':
                queb += 1
        val = tot - queb
        return tot, real, round((real / val) * 100, 1) if val > 0 else 0

    lista_grupos = []
    for nome in sorted(blocos_map):
        b = blocos_map[nome]
        subs = []
        for nome_sub in sorted(b['subs']):
            s = b['subs'][nome_sub]
            faixas = []
            for t in sorted(s['turnos'], key=ordem_turno):
                f = s['turnos'][t]
                # Uma linha da grade recebe um conjunto por dia. Assim a faixa
                # fica com linhas de mesma altura, em vez de uma célula gigante
                # empilhando tudo e desalinhando o resto da tabela.
                por_dia_conj = {}
                for data, conj in f['dias'].items():
                    por_dia_conj[data] = [{'chave': k, 'itens': v}
                                          for k, v in sorted(conj.items())]
                n_linhas = max((len(v) for v in por_dia_conj.values()), default=1)
                for i in range(n_linhas):
                    dias_linha = {}
                    for data, lista in por_dia_conj.items():
                        if i < len(lista):
                            dias_linha[data] = lista[i]
                    faixas.append({'turno': f['turno'] if i == 0 else '',
                                   'horario': f['horario'] if i == 0 else '',
                                   'rowspan_turno': n_linhas if i == 0 else 0,
                                   'dias': dias_linha})
            subs.append({'nome': nome_sub, 'qtd_frotas': len(s['frotas']), 'linhas': faixas})

        # O template desenha uma linha por faixa; achatar aqui evita calcular
        # rowspan dentro do Jinja, onde é fácil errar por um.
        linhas = []
        for i_sub, s in enumerate(subs):
            for i_faixa, f in enumerate(s['linhas']):
                linhas.append({
                    'sub_nome': s['nome'], 'sub_qtd': s['qtd_frotas'],
                    'turno': f['turno'], 'horario': f['horario'],
                    'rowspan_turno': f['rowspan_turno'], 'dias': f['dias'],
                    'abre_bloco': i_sub == 0 and i_faixa == 0,
                    'abre_sub': i_faixa == 0,
                    'rowspan_sub': len(s['linhas']),
                })
        for ln in linhas:
            ln['rowspan_bloco'] = len(linhas)

        classes_bloco = [c['classe']
                         for s in b['subs'].values()
                         for f in s['turnos'].values()
                         for conj in f['dias'].values()
                         for itens in conj.values()
                         for c in itens]
        tot, real, ader = medir(classes_bloco)
        lista_grupos.append({
            'nome': nome, 'linhas': linhas, 'qtd_frotas': len(b['frotas']),
            'varios_subs': len(subs) > 1,
            'sub_unico': subs[0]['nome'] if len(subs) == 1 and subs[0]['nome'] not in ('SEM FRENTE', 'SEM SETOR') else '',
            'total': tot, 'realizadas': real, 'aderencia': ader
        })

    # Totais por dia, a faixa de baixo da planilha.
    por_dia = []
    for dia in dias:
        tot = real = queb = 0
        for r in registros:
            if r['data_planejada'] != dia['db']:
                continue
            tot += 1
            c = classe_status(r)
            if c in ('ok', 'atraso'):
                real += 1
            elif c == 'parado':
                queb += 1
        val = tot - queb
        por_dia.append({'total': tot, 'realizadas': real,
                        'aderencia': round((real / val) * 100, 1) if val > 0 else 0})

    resumo = {'total': 0, 'ok': 0, 'atraso': 0, 'pendente': 0, 'manutencao': 0, 'parado': 0}
    for r in registros:
        resumo['total'] += 1
        resumo[classe_status(r)] += 1
    valido = resumo['total'] - resumo['parado']
    resumo['realizadas'] = resumo['ok'] + resumo['atraso']
    resumo['aderencia'] = round((resumo['realizadas'] / valido) * 100, 1) if valido > 0 else 0

    com_prog = [g for g in lista_grupos if g['total'] > 0]
    melhor = max(com_prog, key=lambda g: g['aderencia']) if com_prog else None
    pior = min(com_prog, key=lambda g: g['aderencia']) if com_prog else None

    # Evolução por semana do período.
    semanas = {}
    for r in registros:
        sem = datetime.strptime(r['data_planejada'], '%Y-%m-%d').isocalendar()[1]
        acc = semanas.setdefault(sem, {'total': 0, 'real': 0, 'queb': 0})
        acc['total'] += 1
        c = classe_status(r)
        if c in ('ok', 'atraso'):
            acc['real'] += 1
        elif c == 'parado':
            acc['queb'] += 1
    evolucao = []
    for i, sem in enumerate(sorted(semanas), start=1):
        a = semanas[sem]
        v = a['total'] - a['queb']
        evolucao.append({'rotulo': f'SEM {i}',
                         'aderencia': round((a['real'] / v) * 100, 1) if v > 0 else 0,
                         'realizadas': a['real'], 'pendentes': a['total'] - a['real'],
                         'total': a['total']})

    tem_turno = any(r['turno'] for r in registros)
    tem_conjunto = any(r['conjunto'] for r in registros)

    nomes_meses = ['JANEIRO', 'FEVEREIRO', 'MARÇO', 'ABRIL', 'MAIO', 'JUNHO', 'JULHO',
                   'AGOSTO', 'SETEMBRO', 'OUTUBRO', 'NOVEMBRO', 'DEZEMBRO']
    return render_template('relatorio_preventiva_setor.html',
        setor=setor or 'TODOS OS SETORES',
        rotulo_agrupamento='SETOR', rotulo_sub='FRENTE',
        tem_turno=tem_turno, tem_conjunto=tem_conjunto,
        tipo=tipo, dias=dias, grupos=lista_grupos, por_dia=por_dia,
        resumo=resumo, melhor=melhor, pior=pior, evolucao=evolucao,
        periodo_txt=f"{d_ini.strftime('%d/%m/%Y')} a {d_fim.strftime('%d/%m/%Y')}",
        mes_txt=f"{nomes_meses[d_ini.month - 1]} {d_ini.year}",
        data_geracao=datetime.now().strftime('%d/%m/%Y %H:%M:%S'))


@app.route('/api/classificar_programacao', methods=['POST'])
def api_classificar_programacao():
    """Roda o classificador no período e, se pedido, grava os status encontrados.

    Com `simular=1` só devolve o que mudaria - serve para conferir antes de
    aplicar, já que a gravação sobrescreve o status manual da programação.
    """
    if not session.get('logado'):
        return jsonify({'erro': 'nao autenticado'}), 401
    if not tem_acesso_modulo('programacao'):
        return jsonify({'erro': 'sem permissao'}), 403

    dados = request.get_json(silent=True) or {}
    inicio = (dados.get('inicio') or '').strip()
    fim = (dados.get('fim') or '').strip()
    if not inicio or not fim:
        return jsonify({'erro': 'informe inicio e fim'}), 400

    fazer_prev = bool(dados.get('preventiva', True))
    fazer_pit = bool(dados.get('pitstop', True))
    simular = bool(dados.get('simular', False))

    conn = get_db_connection()
    total_os = conn.execute('SELECT COUNT(*) as n FROM programacao_os_import').fetchone()['n']
    if not total_os:
        conn.close()
        return jsonify({
            'erro': 'sem_os',
            'msg': 'Nenhuma O.S. importada. Envie a base Mensal e/ou Abertas antes de classificar.'
        }), 400

    try:
        resultados = classificar_programacao(conn, inicio, fim, fazer_prev, fazer_pit)
    except Exception as e:
        conn.close()
        return jsonify({'erro': 'falha', 'msg': str(e)}), 500

    mudancas = [r for r in resultados if r['status_novo'] != (r['status_atual'] or 'PENDENTE')]

    aplicados = 0
    if not simular and mudancas:
        for r in mudancas:
            os_vinculada = r['os'] or ''
            motivo_classificacao = r['motivo'] or ''
            # data_execucao só faz sentido quando a O.S. de fato fechou o ciclo.
            if r['status_novo'] == 'REALIZADA' and r['os_liberacao']:
                dia, mes, ano = r['os_liberacao'].split('/')
                conn.execute(
                    '''UPDATE programacao_semanal
                       SET status = ?, data_execucao = ?, origem_classificacao = 'AUTO',
                           os_vinculada = ?, motivo_classificacao = ?
                       WHERE id = ?''',
                    (r['status_novo'], f'{ano}-{mes}-{dia}', os_vinculada, motivo_classificacao, r['id']))
            else:
                conn.execute(
                    '''UPDATE programacao_semanal
                       SET status = ?, origem_classificacao = 'AUTO',
                           os_vinculada = ?, motivo_classificacao = ?
                       WHERE id = ?''',
                    (r['status_novo'], os_vinculada, motivo_classificacao, r['id']))
            aplicados += 1
        conn.commit()
    conn.close()

    resumo = {'REALIZADA': 0, 'PENDENTE': 0, 'BLOQUEADA': 0, 'QUEBRADA': 0}
    atrasos = 0
    for r in resultados:
        resumo[r['status_novo']] = resumo.get(r['status_novo'], 0) + 1
        if r['com_atraso']:
            atrasos += 1

    return jsonify({
        'periodo': {'inicio': inicio, 'fim': fim},
        'simulado': simular,
        'os_na_base': total_os,
        'analisados': len(resultados),
        'mudancas': len(mudancas),
        'aplicados': aplicados,
        'com_atraso': atrasos,
        'resumo': resumo,
        'preventivas': sum(1 for r in resultados if r['tipo'] == 'PREVENTIVA'),
        'pitstops': sum(1 for r in resultados if r['tipo'] == 'PITSTOP'),
        'frotas': len({r['frota'] for r in resultados}),
        'detalhe': mudancas[:200],
    })


@app.route('/api/ferramentaria/analitico')
def api_ferramentaria_analitico():
    """Recalcula os números do Analítico para um intervalo de datas.

    O seletor de período existia só para montar o link do PDF - os gráficos da
    tela ficavam sempre no acumulado, o que fazia o filtro parecer quebrado.
    Aqui o mesmo recorte vale para o que está na tela.
    """
    if not session.get('logado'):
        return jsonify({'erro': 'nao autenticado'}), 401
    if not tem_acesso_modulo('ferramentaria'):
        return jsonify({'erro': 'sem permissao'}), 403

    hoje = datetime.now()
    periodo = (request.args.get('periodo') or 'semana').strip().lower()
    if periodo == 'dia':
        ini = fim = hoje
    elif periodo == 'mes':
        ini = hoje.replace(day=1)
        fim = hoje
    elif periodo == 'personalizado':
        try:
            ini = datetime.strptime(request.args.get('data_ini', ''), '%Y-%m-%d')
            fim = datetime.strptime(request.args.get('data_fim', ''), '%Y-%m-%d')
        except ValueError:
            return jsonify({'erro': 'datas invalidas'}), 400
    else:  # semana
        ini = hoje - timedelta(days=hoje.weekday())
        fim = ini + timedelta(days=6)

    d_ini, d_fim = ini.strftime('%Y-%m-%d'), fim.strftime('%Y-%m-%d')
    conn = get_db_connection()
    par = (d_ini, d_fim)

    top_colab = [dict(r) for r in conn.execute('''
        SELECT r.mecanico, SUM(i.quantidade) as total
        FROM ferramentaria_retiradas r
        JOIN ferramentaria_retirada_itens i ON i.retirada_id = r.id
        WHERE r.data_devolucao IS NULL AND r.data_retirada BETWEEN ? AND ?
        GROUP BY r.mecanico ORDER BY total DESC LIMIT 10
    ''', par).fetchall()]

    top_tempo = [dict(r) for r in conn.execute('''
        SELECT mecanico, MIN(data_retirada) as desde,
               CAST(julianday('now') - julianday(MIN(data_retirada)) AS INTEGER) as dias
        FROM ferramentaria_retiradas
        WHERE data_devolucao IS NULL AND data_retirada BETWEEN ? AND ?
        GROUP BY mecanico ORDER BY dias DESC LIMIT 10
    ''', par).fetchall()]

    top_ferr = [dict(r) for r in conn.execute('''
        SELECT COALESCE(NULLIF(TRIM(i.codigo), ''), '-') as codigo, i.descricao,
               SUM(i.quantidade) as total
        FROM ferramentaria_retirada_itens i
        JOIN ferramentaria_retiradas r ON r.id = i.retirada_id
        WHERE r.data_retirada BETWEEN ? AND ?
        GROUP BY COALESCE(NULLIF(TRIM(i.codigo), ''), i.descricao), i.descricao
        ORDER BY total DESC LIMIT 10
    ''', par).fetchall()]

    taxa = [dict(r) for r in conn.execute('''
        SELECT mecanico, COUNT(*) as total_retiradas,
               SUM(CASE WHEN data_devolucao IS NOT NULL THEN 1 ELSE 0 END) as devolvidas,
               SUM(CASE WHEN data_devolucao IS NULL     THEN 1 ELSE 0 END) as pendentes,
               ROUND(100.0 * SUM(CASE WHEN data_devolucao IS NOT NULL THEN 1 ELSE 0 END) / COUNT(*), 1) as taxa_pct
        FROM ferramentaria_retiradas
        WHERE data_retirada BETWEEN ? AND ?
        GROUP BY mecanico HAVING total_retiradas > 0
        ORDER BY pendentes DESC, total_retiradas DESC LIMIT 15
    ''', par).fetchall()]

    valor = [dict(r) for r in conn.execute('''
        SELECT r.mecanico, COUNT(DISTINCT r.id) as num_retiradas,
               SUM(i.quantidade * i.valor_unitario) as valor_total
        FROM ferramentaria_retiradas r
        JOIN ferramentaria_retirada_itens i ON i.retirada_id = r.id
        WHERE r.data_devolucao IS NULL AND i.valor_unitario > 0
          AND r.data_retirada BETWEEN ? AND ?
        GROUP BY r.mecanico ORDER BY valor_total DESC LIMIT 15
    ''', par).fetchall()]

    ind = conn.execute('''
        SELECT COUNT(*) as total_retiradas,
               SUM(CASE WHEN data_devolucao IS NULL     THEN 1 ELSE 0 END) as em_aberto,
               SUM(CASE WHEN data_devolucao IS NOT NULL THEN 1 ELSE 0 END) as devolvidas,
               COUNT(DISTINCT CASE WHEN data_devolucao IS NULL THEN mecanico END) as colaboradores_com_posse,
               MAX(CASE WHEN data_devolucao IS NULL
                        THEN CAST(julianday('now') - julianday(data_retirada) AS INTEGER) END) as maior_posse_dias
        FROM ferramentaria_retiradas WHERE data_retirada BETWEEN ? AND ?
    ''', par).fetchone()
    valor_aberto = conn.execute('''
        SELECT COALESCE(SUM(i.quantidade * i.valor_unitario), 0) as v
        FROM ferramentaria_retirada_itens i
        JOIN ferramentaria_retiradas r ON r.id = i.retirada_id
        WHERE r.data_devolucao IS NULL AND r.data_retirada BETWEEN ? AND ?
    ''', par).fetchone()
    conn.close()

    tot = int(ind['total_retiradas'] or 0)
    return jsonify({
        'periodo': {'inicio': d_ini, 'fim': d_fim},
        'top_colaboradores': top_colab,
        'top_tempo': top_tempo,
        'top_ferramentas': top_ferr,
        'taxa_devolucao': taxa,
        'valor_emprestado': valor,
        'indicadores': {
            'total_retiradas': tot,
            'em_aberto': int(ind['em_aberto'] or 0),
            'devolvidas': int(ind['devolvidas'] or 0),
            'taxa_devolucao_geral': round((int(ind['devolvidas'] or 0) / tot) * 100, 1) if tot else 0,
            'colaboradores_com_posse': int(ind['colaboradores_com_posse'] or 0),
            'maior_posse_dias': int(ind['maior_posse_dias'] or 0),
            'valor_em_aberto': float(valor_aberto['v'] or 0),
        }
    })


@app.route('/api/ferramentaria/registrar', methods=['POST'])
def registrar_retirada_ferramentaria():
    if not session.get('logado'): return redirect(url_for('login'))
    if not tem_acesso_modulo('ferramentaria'):
        flash('Seu perfil não tem permissão para registrar retiradas da ferramentaria.', 'danger')
        return redirect(url_for('admin'))

    mecanico = request.form.get('mecanico', '').strip().upper()
    data_retirada = request.form.get('data_retirada', '').strip()
    frota = request.form.get('frota', '').strip().upper()
    local = request.form.get('local', '').strip().upper()
    observacao = request.form.get('observacao', '').strip()

    codigos = request.form.getlist('codigo[]')
    descricoes = request.form.getlist('descricao[]')
    quantidades = request.form.getlist('quantidade[]')
    valores = request.form.getlist('valor_unitario[]')

    itens = []
    for idx, descricao in enumerate(descricoes):
        desc = (descricao or '').strip().upper()
        codigo = (codigos[idx] if idx < len(codigos) else '').strip()
        if not desc and not codigo:
            continue
        try:
            qtd = float(str(quantidades[idx] if idx < len(quantidades) else '1').replace(',', '.'))
        except:
            qtd = 1
        try:
            valor = float(str(valores[idx] if idx < len(valores) else '0').replace(',', '.'))
        except:
            valor = 0
        if qtd <= 0:
            qtd = 1
        itens.append({
            'codigo': codigo,
            'descricao': desc or codigo,
            'quantidade': qtd,
            'valor_unitario': valor
        })

    if not mecanico or not data_retirada or not itens:
        flash('Informe o mecânico, a data e pelo menos uma ferramenta.', 'danger')
        return redirect(url_for('ferramentaria'))

    conn = get_db_connection()
    limite_duplicidade = (datetime.now() - timedelta(minutes=2)).strftime('%Y-%m-%d %H:%M:%S')
    duplicada = conn.execute('''
        SELECT r.id
        FROM ferramentaria_retiradas r
        WHERE r.mecanico = ?
          AND r.data_retirada = ?
          AND IFNULL(r.frota, '') = ?
          AND IFNULL(r.local, '') = ?
          AND r.criado_em >= ?
        ORDER BY r.id DESC
        LIMIT 1
    ''', (mecanico, data_retirada, frota, local, limite_duplicidade)).fetchone()
    if duplicada:
        conn.close()
        flash('Este termo já foi registrado há poucos instantes. Evitamos duplicar o histórico.', 'warning')
        return redirect(url_for('ferramentaria', aba='historico'))

    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO ferramentaria_retiradas (mecanico, frota, local, data_retirada, observacao, criado_por, criado_em)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (
        mecanico,
        frota,
        local,
        data_retirada,
        observacao,
        session.get('perfil_nome', ''),
        datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    ))
    retirada_id = cursor.lastrowid
    for item in itens:
        cursor.execute('''
            INSERT INTO ferramentaria_retirada_itens (retirada_id, codigo, descricao, quantidade, valor_unitario)
            VALUES (?, ?, ?, ?, ?)
        ''', (retirada_id, item['codigo'], item['descricao'], item['quantidade'], item['valor_unitario']))
        if item['codigo']:
            cursor.execute('''
                UPDATE ferramentaria_ferramentas
                SET estoque_total = CASE
                    WHEN estoque_total - ? < 0 THEN 0
                    ELSE estoque_total - ?
                END,
                atualizado_em = ?
                WHERE codigo = ?
            ''', (item['quantidade'], item['quantidade'], datetime.now().strftime('%Y-%m-%d %H:%M:%S'), item['codigo']))
    conn.commit()
    conn.close()
    flash(f'Retirada registrada para {mecanico}.', 'success')
    return redirect(url_for('ferramentaria', aba='historico'))

@app.route('/api/ferramentaria/historico/excluir/<int:id_retirada>', methods=['POST'])
def excluir_retirada_ferramentaria(id_retirada):
    if not session.get('logado'): return redirect(url_for('login'))
    if not is_admin_session():
        flash('Apenas o administrador pode excluir histórico da Ferramentaria.', 'danger')
        return redirect(url_for('ferramentaria', aba='historico'))

    conn = get_db_connection()
    retirada = conn.execute('SELECT mecanico, data_devolucao FROM ferramentaria_retiradas WHERE id = ?', (id_retirada,)).fetchone()
    if retirada:
        if not retirada['data_devolucao']:
            itens_abertos = conn.execute('''
                SELECT codigo, quantidade
                FROM ferramentaria_retirada_itens
                WHERE retirada_id = ? AND codigo IS NOT NULL AND TRIM(codigo) != ''
            ''', (id_retirada,)).fetchall()
            for item in itens_abertos:
                conn.execute('''
                    UPDATE ferramentaria_ferramentas
                    SET estoque_total = estoque_total + ?, atualizado_em = ?
                    WHERE codigo = ?
                ''', (item['quantidade'], datetime.now().strftime('%Y-%m-%d %H:%M:%S'), item['codigo']))
        conn.execute('DELETE FROM ferramentaria_retirada_itens WHERE retirada_id = ?', (id_retirada,))
        conn.execute('DELETE FROM ferramentaria_retiradas WHERE id = ?', (id_retirada,))
        conn.commit()
        flash(f"Histórico de {retirada['mecanico']} excluído com sucesso.", 'danger')
    else:
        flash('Registro de histórico não encontrado.', 'warning')
    conn.close()
    return redirect(url_for('ferramentaria', aba='historico'))

@app.route('/api/ferramentaria/historico/devolver/<int:id_retirada>', methods=['POST'])
def devolver_retirada_ferramentaria(id_retirada):
    if not session.get('logado'): return redirect(url_for('login'))
    if not tem_acesso_modulo('ferramentaria'):
        flash('Seu perfil não tem permissão para confirmar devoluções.', 'danger')
        return redirect(url_for('admin'))

    conn = get_db_connection()
    retirada = conn.execute('SELECT mecanico, data_devolucao FROM ferramentaria_retiradas WHERE id = ?', (id_retirada,)).fetchone()
    if not retirada:
        flash('Registro de histórico não encontrado.', 'warning')
    elif retirada['data_devolucao']:
        flash('Esta retirada já estava marcada como devolvida.', 'warning')
    else:
        itens_devolvidos = conn.execute('''
            SELECT codigo, quantidade
            FROM ferramentaria_retirada_itens
            WHERE retirada_id = ? AND codigo IS NOT NULL AND TRIM(codigo) != ''
        ''', (id_retirada,)).fetchall()
        for item in itens_devolvidos:
            conn.execute('''
                UPDATE ferramentaria_ferramentas
                SET estoque_total = estoque_total + ?, atualizado_em = ?
                WHERE codigo = ?
            ''', (item['quantidade'], datetime.now().strftime('%Y-%m-%d %H:%M:%S'), item['codigo']))
        conn.execute(
            'UPDATE ferramentaria_retiradas SET data_devolucao = ? WHERE id = ?',
            (datetime.now().strftime('%Y-%m-%d'), id_retirada)
        )
        conn.commit()
        flash(f"Devolução de {retirada['mecanico']} confirmada. As ferramentas voltaram ao estoque disponível.", 'success')
    conn.close()
    return redirect(url_for('ferramentaria', aba='historico'))

@app.route('/api/ferramentaria/ferramentas/buscar')
def buscar_ferramentas_ferramentaria():
    if not session.get('logado'): return jsonify([])
    if not (tem_acesso_modulo('ferramentaria') or tem_acesso_modulo('compras')): return jsonify([])
    termo = request.args.get('q', '').strip().upper()
    if len(termo) < 2:
        return jsonify([])
    conn = get_db_connection()
    rows = conn.execute('''
        SELECT f.codigo, f.descricao,
               COALESCE((
                   SELECT i.valor_unitario
                   FROM ferramentaria_retirada_itens i
                   WHERE i.codigo = f.codigo AND i.valor_unitario > 0
                   ORDER BY i.id DESC LIMIT 1
               ), 0) as ultimo_valor
        FROM ferramentaria_ferramentas f
        WHERE f.ativo = 1 AND (f.codigo LIKE ? OR f.descricao LIKE ?)
        ORDER BY
            CASE WHEN f.codigo = ? THEN 0 WHEN f.codigo LIKE ? THEN 1 ELSE 2 END,
            f.descricao
        LIMIT 20
    ''', (f'%{termo}%', f'%{termo}%', termo, f'{termo}%')).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/ferramentaria/associados/buscar')
def buscar_associados():
    if not session.get('logado'): return jsonify([])
    if not tem_acesso_modulo('ferramentaria'): return jsonify([])
    termo = request.args.get('q', '').strip().upper()
    if len(termo) < 2:
        return jsonify([])
    conn = get_db_connection()
    rows = conn.execute('''
        SELECT matricula, nome
        FROM associados
        WHERE nome LIKE ? OR matricula LIKE ?
        ORDER BY
            CASE WHEN matricula = ? THEN 0 WHEN nome LIKE ? THEN 1 ELSE 2 END,
            nome
        LIMIT 15
    ''', (f'%{termo}%', f'%{termo}%', termo, f'{termo}%')).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/ferramentaria/ferramentas/salvar', methods=['POST'])
def salvar_ferramenta_ferramentaria():
    if not session.get('logado'): return redirect(url_for('login'))
    if not tem_acesso_modulo('ferramentaria'):
        flash('Seu perfil não tem permissão para editar a base de ferramentas.', 'danger')
        return redirect(url_for('admin'))

    ferramenta_id = request.form.get('ferramenta_id', '').strip()
    codigo = request.form.get('codigo', '').strip()
    descricao = request.form.get('descricao', '').strip().upper()
    try:
        estoque_total = float(str(request.form.get('estoque_total', '0')).replace(',', '.'))
    except:
        estoque_total = 0
    try:
        estoque_minimo = float(str(request.form.get('estoque_minimo', '0')).replace(',', '.'))
    except:
        estoque_minimo = 0
    busca = request.form.get('busca_ferramenta', '').strip()
    pagina = request.form.get('pagina_ferramentas', '1').strip() or '1'
    if not codigo or not descricao:
        flash('Informe código e descrição da ferramenta.', 'danger')
        return redirect(url_for('ferramentaria', busca_ferramenta=busca, pagina_ferramentas=pagina, aba='base'))

    conn = get_db_connection()
    try:
        if ferramenta_id and ferramenta_id.isdigit():
            conn.execute('''
                UPDATE ferramentaria_ferramentas
                SET codigo = ?, descricao = ?, estoque_total = ?, estoque_minimo = ?, ativo = 1, atualizado_em = ?
                WHERE id = ?
            ''', (codigo, descricao, estoque_total, estoque_minimo, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), ferramenta_id))
            flash('Ferramenta atualizada com sucesso.', 'warning')
        else:
            existente = conn.execute('SELECT id, ativo FROM ferramentaria_ferramentas WHERE codigo = ?', (codigo,)).fetchone()
            if existente:
                conn.execute('''
                    UPDATE ferramentaria_ferramentas
                    SET descricao = ?, estoque_total = ?, estoque_minimo = ?, ativo = 1, atualizado_em = ?
                    WHERE id = ?
                ''', (descricao, estoque_total, estoque_minimo, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), existente['id']))
                flash('Ferramenta reativada e atualizada na base.', 'success')
            else:
                conn.execute('''
                    INSERT INTO ferramentaria_ferramentas (codigo, descricao, estoque_total, estoque_minimo, ativo, atualizado_em)
                    VALUES (?, ?, ?, ?, 1, ?)
                ''', (codigo, descricao, estoque_total, estoque_minimo, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
                flash('Ferramenta adicionada à base.', 'success')
        conn.commit()
    except sqlite3.IntegrityError:
        flash('Já existe uma ferramenta com esse código.', 'danger')
    finally:
        conn.close()
    return redirect(url_for('ferramentaria', busca_ferramenta=busca, pagina_ferramentas=pagina, aba='base'))

@app.route('/api/ferramentaria/ferramentas/excluir/<int:id_ferramenta>', methods=['POST'])
def excluir_ferramenta_ferramentaria(id_ferramenta):
    if not session.get('logado'): return redirect(url_for('login'))
    if not tem_acesso_modulo('ferramentaria'):
        flash('Seu perfil não tem permissão para excluir ferramentas.', 'danger')
        return redirect(url_for('admin'))
    busca = request.form.get('busca_ferramenta', '').strip()
    pagina = request.form.get('pagina_ferramentas', '1').strip() or '1'
    conn = get_db_connection()
    conn.execute('UPDATE ferramentaria_ferramentas SET ativo = 0, atualizado_em = ? WHERE id = ?', (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), id_ferramenta))
    conn.commit()
    conn.close()
    flash('Ferramenta removida da base.', 'danger')
    return redirect(url_for('ferramentaria', busca_ferramenta=busca, pagina_ferramentas=pagina, aba='base'))

@app.route('/ferramentaria/termo/<int:id_retirada>')
def termo_ferramentaria(id_retirada):
    if not session.get('logado'): return redirect(url_for('login'))
    if not tem_acesso_modulo('ferramentaria'):
        flash('Seu perfil não tem permissão para acessar Ferramentaria.', 'danger')
        return redirect(url_for('admin'))

    conn = get_db_connection()
    retirada = conn.execute('SELECT * FROM ferramentaria_retiradas WHERE id = ?', (id_retirada,)).fetchone()
    if not retirada:
        conn.close()
        flash('Termo não encontrado.', 'danger')
        return redirect(url_for('ferramentaria'))
    itens = conn.execute('''
        SELECT codigo, descricao, quantidade, valor_unitario,
               quantidade * valor_unitario as valor_total
        FROM ferramentaria_retirada_itens
        WHERE retirada_id = ?
        ORDER BY id
    ''', (id_retirada,)).fetchall()
    conn.close()
    itens_formatados = []
    for i in itens:
        item = dict(i)
        item['quantidade_fmt'] = formatar_quantidade_ferramenta(item.get('quantidade'))
        itens_formatados.append(item)
    itens = itens_formatados
    total = sum(float(i.get('valor_total') or 0) for i in itens)
    return render_template('termo_ferramentaria.html', retirada=dict(retirada), itens=itens, total=total)


@app.route('/ferramentaria/caminhoes/<int:caminhao_id>/ficha')
def ficha_caminhao_oficina(caminhao_id):
    if not session.get('logado'): return redirect(url_for('login'))
    if not tem_acesso_modulo('ferramentaria'):
        flash('Seu perfil não tem permissão para acessar Ferramentaria.', 'danger')
        return redirect(url_for('admin'))

    conn = get_db_connection()
    caminhao = conn.execute('SELECT * FROM caminhao_oficina WHERE id = ?', (caminhao_id,)).fetchone()
    if not caminhao:
        conn.close()
        flash('Caminhão oficina não encontrado.', 'danger')
        return redirect(url_for('ferramentaria', aba='caminhoes'))
    itens_raw = conn.execute('''
        SELECT codigo, descricao, quantidade, valor_unitario,
               quantidade * valor_unitario as valor_total
        FROM caminhao_oficina_itens
        WHERE caminhao_id = ?
        ORDER BY id
    ''', (caminhao_id,)).fetchall()
    conn.close()

    itens = []
    for i in itens_raw:
        item = dict(i)
        item['quantidade_fmt'] = formatar_quantidade_ferramenta(item.get('quantidade'))
        itens.append(item)
    total = sum(float(i.get('valor_total') or 0) for i in itens)
    return render_template('ficha_caminhao_oficina.html', caminhao=dict(caminhao), itens=itens, total=total,
                            hoje=datetime.now().strftime('%d/%m/%Y'))


@app.route('/ferramentaria/caminhoes/relatorio_geral')
def relatorio_geral_caminhoes():
    if not session.get('logado'): return redirect(url_for('login'))
    if not tem_acesso_modulo('ferramentaria'):
        flash('Seu perfil não tem permissão para acessar Ferramentaria.', 'danger')
        return redirect(url_for('admin'))

    conn = get_db_connection()

    # Tabela 1: estoque geral — tudo que está cadastrado em cada caminhão
    itens_raw = conn.execute('''
        SELECT c.identificacao, c.responsavel,
               i.codigo, i.descricao, i.quantidade, i.valor_unitario, i.observacao,
               i.quantidade * i.valor_unitario as valor_total
        FROM caminhao_oficina c
        JOIN caminhao_oficina_itens i ON i.caminhao_id = c.id
        ORDER BY c.identificacao, i.descricao
    ''').fetchall()
    itens_geral = []
    for i in itens_raw:
        item = dict(i)
        item['quantidade_fmt'] = formatar_quantidade_ferramenta(item.get('quantidade'))
        itens_geral.append(item)
    valor_total_geral = sum(float(i.get('valor_total') or 0) for i in itens_geral)

    # Tabela 2: itens com observação registrada (possíveis danificados/em atenção)
    itens_observacao = [i for i in itens_geral if (i.get('observacao') or '').strip()]

    # Tabela 3: ferramentas faltando na ÚLTIMA conferência registrada de cada caminhão
    faltando_raw = conn.execute('''
        SELECT co.data_conferencia, c.identificacao, c.responsavel,
               ci.codigo, ci.descricao, ci.quantidade_esperada, ci.observacao
        FROM caminhao_conferencias co
        JOIN caminhao_oficina c ON c.id = co.caminhao_id
        JOIN caminhao_conferencia_itens ci ON ci.conferencia_id = co.id
        WHERE co.id IN (SELECT MAX(id) FROM caminhao_conferencias GROUP BY caminhao_id)
          AND ci.presente = 0
        ORDER BY c.identificacao, ci.descricao
    ''').fetchall()
    itens_faltando = [dict(r) for r in faltando_raw]

    conn.close()
    return render_template('relatorio_geral_caminhoes.html',
                            itens_geral=itens_geral,
                            valor_total_geral=valor_total_geral,
                            itens_observacao=itens_observacao,
                            itens_faltando=itens_faltando,
                            hoje=datetime.now().strftime('%d/%m/%Y %H:%M'))


@app.route('/api/upload_chb', methods=['POST'])
def upload_chb():
    if not session.get('logado'): return {"status": "erro"}, 403
    if 'file' not in request.files: flash('Nenhum ficheiro selecionado.', 'danger'); return redirect(url_for('externos'))
    file = request.files['file']
    filename = file.filename.lower()
    if filename == '': flash('Nenhum ficheiro selecionado.', 'danger'); return redirect(url_for('externos'))
    conn = get_db_connection()
    count_novos = 0; count_atualizados = 0; count_removidos = 0
    os_processadas = []
    os_para_fechar = []
    import unicodedata

    def normalizar_chave(valor):
        txt = str(valor or '').strip().upper()
        txt = unicodedata.normalize('NFKD', txt)
        txt = ''.join(ch for ch in txt if not unicodedata.combining(ch))
        txt = ' '.join(txt.split())
        return txt

    def obter_valor(mapa, chaves):
        for chave in chaves:
            v = mapa.get(chave)
            if v is not None and str(v).strip() != '':
                return str(v).strip()
        return ''

    try:
        if filename.endswith('.xlsx'):
            try: import pandas as pd
            except ImportError: flash('Para ler Excel instale "pandas". Salve como CSV.', 'warning'); return redirect(url_for('externos'))
            df = pd.read_excel(file, header=None)
            header_idx = -1
            for i, row in df.iterrows():
                cabecalhos = [normalizar_chave(cell) for cell in row.values]
                if 'NRO OS' in cabecalhos:
                    header_idx = i
                    break
            if header_idx == -1: flash('Cabeçalho "Nro OS" não encontrado.', 'danger'); return redirect(url_for('externos'))
            df.columns = df.iloc[header_idx].astype(str).str.strip()
            df = df.iloc[header_idx + 1:]
            for _, row in df.iterrows():
                row_norm = {normalizar_chave(k): ('' if pd.isna(v) else str(v).strip()) for k, v in row.items()}

                nro_os = obter_valor(row_norm, ['NRO OS', 'NUMERO OS', 'NR OS'])
                if nro_os == 'nan' or not nro_os: continue
                if nro_os.endswith('.0'): nro_os = nro_os[:-2]

                os_processadas.append(nro_os)

                status = obter_valor(row_norm, ['STATUS'])
                data_abertura = obter_valor(row_norm, ['DATA'])[:10]
                frota = obter_valor(row_norm, ['VEICULO', 'FROTA'])
                desc_veiculo = obter_valor(row_norm, ['DESCRICAO VEICULO', 'DESCRICAO'])
                fornecedor = obter_valor(row_norm, ['NOME OFICINA PREVISTA', 'OFICINA PREVISTA'])
                if frota.endswith('.0'): frota = frota[:-2]

                if not frota: continue
                existente = conn.execute("SELECT id_frota FROM frotas WHERE id_frota = ?", (frota,)).fetchone()
                if not existente:
                    conn.execute("INSERT INTO frotas (id_frota, descricao, setor, especialidade, agrupamento) VALUES (?, ?, ?, 'OUTROS', 'GERAL')", (frota, desc_veiculo if desc_veiculo else 'SEM DESCRIÇÃO', ''))
                else:
                    if desc_veiculo and desc_veiculo.upper() not in ['', 'NONE', 'SEM DESCRIÇÃO']:
                        conn.execute("UPDATE frotas SET descricao = ? WHERE id_frota = ? AND (descricao IS NULL OR TRIM(descricao) = '' OR descricao = 'SEM DESCRIÇÃO')", (desc_veiculo, frota))

                os_existe = conn.execute("SELECT nro_os, status_os FROM servicos_externos WHERE nro_os = ?", (nro_os,)).fetchone()
                if os_existe:
                    if os_existe['status_os'] == 'F' and status in ['A', 'E']:
                        # OS estava fechada mas voltou para aberta na planilha â€” reabre
                        conn.execute('UPDATE servicos_externos SET status_os = ?, fornecedor = ?, id_frota = ?, data_fechamento = NULL WHERE nro_os = ?', (status, fornecedor, frota, nro_os)); count_novos += 1
                    elif os_existe['status_os'] != 'F':
                        conn.execute('UPDATE servicos_externos SET status_os = ?, fornecedor = ?, id_frota = ? WHERE nro_os = ?', (status, fornecedor, frota, nro_os)); count_atualizados += 1
                else:
                    if status in ['A', 'E']:
                        conn.execute('INSERT INTO servicos_externos (nro_os, id_frota, fornecedor, data_abertura, status_os) VALUES (?, ?, ?, ?, ?)', (nro_os, frota, fornecedor, data_abertura, status)); count_novos += 1
        else:
            content_bytes = file.read()
            try: content_str = content_bytes.decode('utf-8-sig')
            except: content_str = content_bytes.decode('latin1', errors='ignore')
            content = content_str.splitlines()
            header_idx = -1
            for i, line in enumerate(content):
                if 'NRO OS' in normalizar_chave(line):
                    header_idx = i
                    break
            if header_idx == -1: flash('Coluna "Nro OS" não encontrada.', 'danger'); return redirect(url_for('externos'))
            delimiter = ';' if ';' in content[header_idx] else ','
            csv_reader = csv.DictReader(content[header_idx:], delimiter=delimiter)
            for raw_row in csv_reader:
                row = {normalizar_chave(k): (str(v).strip() if v is not None else '') for k, v in raw_row.items() if k}
                nro_os = obter_valor(row, ['NRO OS', 'NUMERO OS', 'NR OS'])
                if not nro_os: continue
                if nro_os.endswith('.0'): nro_os = nro_os[:-2]

                os_processadas.append(nro_os)

                status = obter_valor(row, ['STATUS'])
                data_abertura = obter_valor(row, ['DATA'])[:10]
                frota = obter_valor(row, ['VEICULO', 'FROTA'])
                desc_veiculo = obter_valor(row, ['DESCRICAO VEICULO', 'DESCRICAO'])
                fornecedor = obter_valor(row, ['NOME OFICINA PREVISTA', 'OFICINA PREVISTA'])
                if frota.endswith('.0'): frota = frota[:-2]

                if not frota: continue
                existente = conn.execute("SELECT id_frota FROM frotas WHERE id_frota = ?", (frota,)).fetchone()
                if not existente:
                    conn.execute("INSERT INTO frotas (id_frota, descricao, setor, especialidade, agrupamento) VALUES (?, ?, ?, 'OUTROS', 'GERAL')", (frota, desc_veiculo if desc_veiculo else 'SEM DESCRIÇÃO', ''))
                else:
                    if desc_veiculo and desc_veiculo.upper() not in ['', 'NONE', 'SEM DESCRIÇÃO']:
                        conn.execute("UPDATE frotas SET descricao = ? WHERE id_frota = ? AND (descricao IS NULL OR TRIM(descricao) = '' OR descricao = 'SEM DESCRIÇÃO')", (desc_veiculo, frota))

                os_existe = conn.execute("SELECT nro_os, status_os FROM servicos_externos WHERE nro_os = ?", (nro_os,)).fetchone()
                if os_existe:
                    if os_existe['status_os'] == 'F' and status in ['A', 'E']:
                        conn.execute('UPDATE servicos_externos SET status_os = ?, fornecedor = ?, id_frota = ?, data_fechamento = NULL WHERE nro_os = ?', (status, fornecedor, frota, nro_os)); count_novos += 1
                    elif os_existe['status_os'] != 'F':
                        conn.execute('UPDATE servicos_externos SET status_os = ?, fornecedor = ?, id_frota = ? WHERE nro_os = ?', (status, fornecedor, frota, nro_os)); count_atualizados += 1
                else:
                    if status in ['A', 'E']:
                        conn.execute('INSERT INTO servicos_externos (nro_os, id_frota, fornecedor, data_abertura, status_os) VALUES (?, ?, ?, ?, ?)', (nro_os, frota, fornecedor, data_abertura, status)); count_novos += 1

        if os_processadas:
            todas_os_db = conn.execute("SELECT nro_os, id_frota FROM servicos_externos WHERE status_os IN ('A', 'E')").fetchall()
            for os_db in todas_os_db:
                if os_db['nro_os'] not in os_processadas:
                    os_para_fechar.append({'nro_os': os_db['nro_os'], 'frota': os_db['id_frota']})

        conn.commit();

        if os_para_fechar:
            session['os_para_fechar'] = os_para_fechar
            flash(f'Base lida! {count_novos} Novas e {count_atualizados} Atualizadas. Frotas ausentes identificadas!', 'warning')
        else:
            flash(f'Base Espelhada com Sucesso! {count_novos} Novas e {count_atualizados} Atualizadas.', 'success')

    except Exception as e: flash(f'Erro: {str(e)}', 'danger')
    finally: conn.close()
    return redirect(url_for('externos'))

@app.route('/api/confirmar_fechamento', methods=['POST'])
def confirmar_fechamento():
    if not session.get('logado'): return redirect(url_for('login'))
    conn = get_db_connection()
    for key, value in request.form.items():
        if key.startswith('data_fecho_'):
            nro_os = key.replace('data_fecho_', '')
            data_fecho = value
            if data_fecho:
                conn.execute("UPDATE servicos_externos SET status_os = 'F', data_fechamento = ? WHERE nro_os = ?", (data_fecho, nro_os))
    conn.commit()
    conn.close()
    flash('Frotas encerradas com sucesso e guardadas no Histórico!', 'success')
    return redirect(url_for('externos'))

@app.route('/api/atualizar_externo', methods=['POST'])
def atualizar_externo():
    if not session.get('logado'): return jsonify({"status": "erro"}), 403
    dados = request.get_json()
    nro_os = dados.get('nro_os')
    acao = dados.get('acao')

    conn = get_db_connection()

    if acao == 'liberar':
        data_fechamento = dados.get('data_fechamento')
        if not data_fechamento: data_fechamento = datetime.now().strftime("%Y-%m-%d")
        conn.execute("UPDATE servicos_externos SET status_os = 'F', data_fechamento = ? WHERE nro_os = ?", (data_fechamento, nro_os))
    else:
        orcamento = 1 if dados.get('orcamento') else 0
        solicitacao = 1 if dados.get('solicitacao') else 0
        cotacao = 1 if dados.get('cotacao') else 0
        pedido = 1 if dados.get('pedido') else 0
        nova_previsao = dados.get('previsao')
        observacao = (dados.get('observacao') or '').strip().upper()
        orcamento_numero = (dados.get('orcamento_numero') or '').strip().upper()
        solicitacao_numero = (dados.get('solicitacao_numero') or '').strip().upper()
        cotacao_numero = (dados.get('cotacao_numero') or '').strip().upper()
        pedido_numero = (dados.get('pedido_numero') or '').strip().upper()

        os_atual = conn.execute("SELECT previsao_atual FROM servicos_externos WHERE nro_os = ?", (nro_os,)).fetchone()
        if os_atual and nova_previsao and os_atual['previsao_atual'] != nova_previsao:
            data_registro = datetime.now().strftime("%d/%m/%Y %H:%M")
            conn.execute("INSERT INTO historico_previsoes_externas (nro_os, data_registro, previsao) VALUES (?, ?, ?)", (nro_os, data_registro, nova_previsao))

        conn.execute('''UPDATE servicos_externos SET orcamento = ?, solicitacao = ?, cotacao = ?, pedido = ?, previsao_atual = ?, observacao = ?,
                         orcamento_numero = ?, solicitacao_numero = ?, cotacao_numero = ?, pedido_numero = ? WHERE nro_os = ?''',
                     (orcamento, solicitacao, cotacao, pedido, nova_previsao, observacao,
                      orcamento_numero, solicitacao_numero, cotacao_numero, pedido_numero, nro_os))

    conn.commit()
    conn.close()
    return jsonify({"status": "sucesso"})

@app.route('/api/upload_abastecimento', methods=['POST'])
def upload_abastecimento():
    if not session.get('logado'): return {"status": "erro"}, 403
    if 'file' not in request.files: flash('Nenhum ficheiro selecionado.', 'danger'); return redirect(url_for('abastecimentos'))
    file = request.files['file']
    filename = file.filename.lower()
    if filename == '': flash('Nenhum ficheiro selecionado.', 'danger'); return redirect(url_for('abastecimentos'))
    conn = get_db_connection()
    count_novos = 0
    try:
        lines_data = []
        if filename.endswith('.xlsx'):
            try: import pandas as pd
            except ImportError:
                flash('Para ler ficheiros Excel instale a biblioteca "pandas". Guarde o ficheiro como .CSV por agora.', 'warning')
                return redirect(url_for('abastecimentos'))
            df = pd.read_excel(file, header=None)
            for _, row in df.iterrows():
                row_vals = []
                for cell in row.values:
                    if pd.isna(cell): row_vals.append('')
                    elif isinstance(cell, datetime): row_vals.append(cell.strftime('%Y-%m-%d'))
                    else: row_vals.append(str(cell).strip())
                lines_data.append(row_vals)
        else:
            content_bytes = file.read()
            try: content_str = content_bytes.decode('utf-8-sig')
            except: content_str = content_bytes.decode('latin1', errors='ignore')
            lines = content_str.splitlines()
            reader = csv.reader(lines, delimiter=';' if lines and ';' in lines[0] and ',' not in lines[0] else ',')
            for row in reader: lines_data.append([c.strip() for c in row])
        current_frota = None
        for row in lines_data:
            if not row: continue
            if len(row) > 1 and ('VEÍCULO' in row[0].upper() or 'VEICULO' in row[0].upper()):
                current_frota = row[1].strip().upper()
                if current_frota.endswith('.0'): current_frota = current_frota[:-2]
                continue
            if current_frota and len(row) >= 5:
                data_val = ""; qtd = ""; km = ""; media = ""
                if len(row) > 8:
                    data_val = row[2]; qtd = row[4]; km = row[8]; media = row[10] if len(row) > 10 else ''
                parsed_date = None
                if len(data_val) >= 10:
                    if '/' in data_val:
                        try: parsed_date = datetime.strptime(data_val[:10], '%d/%m/%Y').strftime('%Y-%m-%d')
                        except: pass
                    elif '-' in data_val:
                        try: parsed_date = datetime.strptime(data_val[:10], '%Y-%m-%d').strftime('%Y-%m-%d')
                        except: pass
                if parsed_date:
                    existente = conn.execute("SELECT id FROM abastecimentos WHERE id_frota=? AND data_abastecimento=? AND quantidade=? AND km_abast=?", (current_frota, parsed_date, qtd, km)).fetchone()
                    if not existente:
                        conn.execute("INSERT INTO abastecimentos (id_frota, data_abastecimento, quantidade, km_abast, media) VALUES (?, ?, ?, ?, ?)", (current_frota, parsed_date, qtd, km, media))
                        count_novos += 1
        conn.commit()
        flash(f'Relatório de Abastecimentos processado com sucesso! {count_novos} novos registos importados.', 'success')
    except Exception as e: flash(f'Erro ao processar ficheiro: {str(e)}', 'danger')
    finally: conn.close()
    return redirect(url_for('abastecimentos'))

@app.route('/abastecimentos')
def abastecimentos():
    if not session.get('logado'): return redirect(url_for('login'))
    if not tem_acesso_modulo('abastecimentos'):
        flash('Seu perfil não tem permissão para acessar Abastecimentos.', 'danger')
        return redirect(url_for('admin'))
    conn = get_db_connection()
    bloqueadas = conn.execute("SELECT h.id, h.id_frota, COALESCE(f.descricao, 'SEM DESCRIÇÃO') as descricao, h.data_bloqueio, h.motivo FROM historico_bloqueios h LEFT JOIN frotas f ON h.id_frota = f.id_frota WHERE h.data_desbloqueio IS NULL ORDER BY h.id_frota, h.id").fetchall()
    bloqueios_por_frota = {}
    for b in bloqueadas:
        frota = b['id_frota']
        if frota not in bloqueios_por_frota:
            bloqueios_por_frota[frota] = {
                'id_frota': frota,
                'descricao': b['descricao'],
                'data_bloqueio': b['data_bloqueio'],
                'motivos': []
            }

        motivos_linha = [m.strip() for m in str(b['motivo'] or '').split('+') if m.strip()]
        for motivo in motivos_linha:
            if motivo not in bloqueios_por_frota[frota]['motivos']:
                bloqueios_por_frota[frota]['motivos'].append(motivo)

        try:
            data_atual = datetime.strptime(str(b['data_bloqueio']).strip(), '%d/%m/%Y')
            data_salva = datetime.strptime(str(bloqueios_por_frota[frota]['data_bloqueio']).strip(), '%d/%m/%Y')
            if data_atual < data_salva:
                bloqueios_por_frota[frota]['data_bloqueio'] = b['data_bloqueio']
        except:
            pass

    frotas_analise = []
    for b in bloqueios_por_frota.values():
        frota = b['id_frota']
        try: db_date = datetime.strptime(b['data_bloqueio'].strip(), '%d/%m/%Y').strftime('%Y-%m-%d')
        except: db_date = '2099-01-01'
        hist_abast = conn.execute('SELECT * FROM abastecimentos WHERE id_frota = ? ORDER BY data_abastecimento DESC, id DESC', (frota,)).fetchall()
        logs = []; abasteceu_bloqueado = False; ultimo_abast = None
        for idx, a in enumerate(hist_abast):
            furou_bloqueio = (a['data_abastecimento'] >= db_date)
            if furou_bloqueio: abasteceu_bloqueado = True
            try: data_br = datetime.strptime(a['data_abastecimento'], '%Y-%m-%d').strftime('%d/%m/%Y')
            except: data_br = a['data_abastecimento']
            if idx == 0: ultimo_abast = {'data': data_br, 'quantidade': a['quantidade'], 'furou': furou_bloqueio}
            logs.append({'data': data_br, 'quantidade': a['quantidade'], 'km': a['km_abast'], 'media': a['media'], 'furou': furou_bloqueio})
        frotas_analise.append({'frota': frota, 'descricao': b['descricao'], 'data_bloqueio': b['data_bloqueio'], 'motivo': " + ".join(b['motivos']) or '-', 'abasteceu_bloqueado': abasteceu_bloqueado, 'ultimo_abast': ultimo_abast, 'logs': logs})
    conn.close()
    return render_template('abastecimentos.html', frotas=frotas_analise)

# ==========================================
# MÓDULO: ANÁLISES CRÍTICAS DE ÓLEO
# ==========================================

def _eh_diagnostico_critico(diagnostico):
    """Critério único de 'análise crítica' usado tanto na fila de pendências quanto
    na proteção contra remoção automática no upload (nenhuma dessas deve sumir do
    relatório sem uma tratativa registrada por alguém)."""
    diag = str(diagnostico or '').upper()
    return any(w in diag for w in ['CRÍTICA', 'CRITICA', 'CRÍTICO', 'CRITICO', 'ALERTA', 'ANOMALIA', 'ATENÇÃO', 'ATENCAO'])


@app.route('/analises')
def analises():
    if not session.get('logado'): return redirect(url_for('login'))
    if not tem_acesso_modulo('analises'):
        flash('Seu perfil não tem permissão para acessar Análises.', 'danger')
        return redirect(url_for('admin'))

    active_tab = request.args.get('aba', 'lista').strip()
    if active_tab not in ['lista', 'historico', 'responsaveis']:
        active_tab = 'lista'

    conn = get_db_connection()

    grupos_raw = conn.execute('SELECT * FROM grupos_responsaveis ORDER BY nome_grupo').fetchall()
    grupo_frotas_raw = conn.execute('''
        SELECT gf.id, gf.grupo_id, gf.id_frota, g.nome_grupo, g.responsavel_nome
        FROM grupo_frotas gf
        JOIN grupos_responsaveis g ON g.id = gf.grupo_id
        ORDER BY gf.id_frota
    ''').fetchall()

    grupos = []
    for g in grupos_raw:
        item_grupo = dict(g)
        item_grupo['frotas'] = [dict(fr) for fr in grupo_frotas_raw if fr['grupo_id'] == g['id']]
        grupos.append(item_grupo)

    grupo_frota_dict = {fr['id_frota']: fr['responsavel_nome'] for fr in grupo_frotas_raw}

    analises_raw = conn.execute('''
        SELECT a.*, COALESCE(f.agrupamento, 'GERAL') as agrupamento, COALESCE(f.especialidade, 'OUTROS') as especialidade
        FROM analises_oleo a
        LEFT JOIN frotas f ON a.id_frota = f.id_frota
        ORDER BY a.data_coleta DESC, a.id DESC
    ''').fetchall()

    # Lista de nomes únicos dos responsáveis por grupo, para popular os seletores
    nomes_responsaveis = sorted({g['responsavel_nome'] for g in grupos_raw if g['responsavel_nome']})

    # Conta, para cada par (frota, compartimento), em quantas DATAS DISTINTAS já apareceu
    # uma análise crítica no histórico — usado para o alerta de reincidência. Usar datas
    # distintas (em vez de contar linhas) evita que uma eventual duplicata (mesma frota,
    # mesmo dia, mesmo compartimento) infle o alerta como se fosse uma recorrência real.
    datas_criticas_por_par = {}
    for a in analises_raw:
        if _eh_diagnostico_critico(a['diagnostico']):
            chave = (a['id_frota'], a['compartimento'])
            datas_criticas_por_par.setdefault(chave, set()).add(a['data_coleta'])
    contagem_critica_compartimento = {chave: len(datas) for chave, datas in datas_criticas_por_par.items()}

    dados_analises = []
    stats = {'total': 0, 'criticas': 0, 'pendentes': 0, 'concluidas': 0}

    for a in analises_raw:
        item = dict(a)
        item['responsavel'] = grupo_frota_dict.get(item['id_frota'], "Não Atribuído")

        stats['total'] += 1

        # CRITÉRIO REFINADO PARA ANÁLISE CRÍTICA
        if _eh_diagnostico_critico(item['diagnostico']):
            item['is_critica'] = True
            # KPI conta só as ainda pendentes — uma vez tratada, ela sai daqui e entra em "concluidas"
            if item['status_tratativa'] != 'CONCLUÍDO':
                stats['criticas'] += 1
            qtd_reincidencia = contagem_critica_compartimento[(item['id_frota'], item['compartimento'])]
            item['reincidente'] = qtd_reincidencia > 1
            item['reincidencia_qtd'] = qtd_reincidencia
        else:
            item['is_critica'] = False
            item['reincidente'] = False
            item['reincidencia_qtd'] = 0

        if item['status_tratativa'] == 'PENDENTE': stats['pendentes'] += 1
        elif item['status_tratativa'] == 'CONCLUÍDO': stats['concluidas'] += 1

        try: item['data_coleta_br'] = datetime.strptime(item['data_coleta'][:10], '%Y-%m-%d').strftime('%d/%m/%Y')
        except: item['data_coleta_br'] = item['data_coleta']

        dados_analises.append(item)

    conn.close()
    return render_template('analises.html', analises=dados_analises, stats=stats,
                            grupos=grupos, nomes_responsaveis=nomes_responsaveis,
                            active_tab=active_tab)


def _classificar_analise_oleo(diagnostico):
    """Replica a classificação usada no dashboard de referência (analise-oleo-frotas):
    CRÍTICO se o diagnóstico contém 'critic'/'crític', NORMAL se vazio ou 'NORMAL',
    e ANOMALIA para qualquer outro achado (ex: VISCOSIDADE FORA, SÍLICA, ÁGUA)."""
    d = (diagnostico or '').strip().upper()
    if 'CRITIC' in d or 'CRÍTIC' in d:
        return 'Critico'
    if not d or d == 'NORMAL':
        return 'Normal'
    return 'Anomalia'


@app.route('/analises/dashboard_pdf')
def analises_dashboard_pdf():
    if not session.get('logado'): return redirect(url_for('login'))
    if not tem_acesso_modulo('analises'):
        flash('Seu perfil não tem permissão para acessar Análises.', 'danger')
        return redirect(url_for('admin'))

    conn = get_db_connection()
    rows = conn.execute('SELECT id_frota, data_coleta, compartimento, diagnostico, parecer FROM analises_oleo ORDER BY data_coleta').fetchall()
    conn.close()

    todas = [dict(r) for r in rows]
    for item in todas:
        item['classificacao'] = _classificar_analise_oleo(item['diagnostico'])

    datas_validas = sorted({item['data_coleta'] for item in todas if item['data_coleta']})

    data_inicio = request.args.get('data_inicio', '').strip()
    data_fim = request.args.get('data_fim', '').strip()
    if not data_inicio and datas_validas: data_inicio = datas_validas[0]
    if not data_fim and datas_validas: data_fim = datas_validas[-1]

    filtradas = [
        item for item in todas
        if item['data_coleta']
        and (not data_inicio or item['data_coleta'] >= data_inicio)
        and (not data_fim or item['data_coleta'] <= data_fim)
    ]

    total = len(filtradas)
    criticas = [i for i in filtradas if i['classificacao'] == 'Critico']
    anomalias = [i for i in filtradas if i['classificacao'] == 'Anomalia']
    datas_unicas = {i['data_coleta'] for i in filtradas if i['data_coleta']}
    media_dia = round(total / len(datas_unicas), 1) if datas_unicas else 0
    taxa_atencao = round((len(anomalias) / total) * 100) if total else 0
    taxa_critica = round((len(criticas) / total) * 100) if total else 0

    risco_counter = Counter((i['compartimento'] or 'Sem compartimento') for i in criticas)
    max_risco = max(risco_counter.values()) if risco_counter else 1
    risco_por_componente = [
        {'nome': nome, 'qtd': qtd, 'largura': max(6, round((qtd / max_risco) * 100))}
        for nome, qtd in sorted(risco_counter.items(), key=lambda kv: -kv[1])[:10]
    ]

    classe_css = {'Normal': 'normal', 'Anomalia': 'anomalia', 'Critico': 'critico'}
    result_counter = Counter(i['classificacao'] for i in filtradas)
    max_result = max(result_counter.values()) if result_counter else 1
    distribuicao_resultado = [
        {'nome': nome, 'qtd': qtd, 'largura': max(6, round((qtd / max_result) * 100)), 'classe': classe_css.get(nome, '')}
        for nome, qtd in sorted(result_counter.items(), key=lambda kv: -kv[1])[:6]
    ]

    cores_pizza = ['#d97706', '#0ea5e9', '#7c3aed', '#16a34a', '#f97316', '#dc2626', '#0891b2', '#a16207']

    def montar_pizza(contador):
        itens = [
            {'nome': nome, 'qtd': qtd, 'cor': cores_pizza[idx % len(cores_pizza)]}
            for idx, (nome, qtd) in enumerate(sorted(contador.items(), key=lambda kv: (-kv[1], kv[0])))
        ]
        total_pizza = sum(i['qtd'] for i in itens)
        if not total_pizza:
            return {'itens': [], 'gradiente': '', 'total': 0}
        cursor = 0.0
        partes = []
        for i in itens:
            inicio = cursor
            cursor += (i['qtd'] / total_pizza) * 100
            partes.append(f"{i['cor']} {inicio}% {cursor}%")
            i['percentual'] = round((i['qtd'] / total_pizza) * 100)
        return {'itens': itens[:7], 'gradiente': ', '.join(partes), 'total': total_pizza}

    comp_anomalia_counter = Counter((i['compartimento'] or 'Sem compartimento') for i in anomalias)
    pizza_compartimento = montar_pizza(comp_anomalia_counter)

    tipo_anomalia_counter = Counter((i['diagnostico'] or 'Anomalia sem descrição') for i in anomalias)
    pizza_tipo = montar_pizza(tipo_anomalia_counter)

    grupos = {}
    for item in anomalias:
        chave = item['diagnostico'] or 'Anomalia sem descrição'
        g = grupos.setdefault(chave, {'anomalia': chave, 'qtd': 0, 'frotas': {}})
        g['qtd'] += 1
        frota = item['id_frota'] or '-'
        g['frotas'].setdefault(frota, set()).add(item['compartimento'] or 'Sem compartimento')

    frotas_com_anomalia = []
    for g in sorted(grupos.values(), key=lambda x: (-x['qtd'], x['anomalia']))[:10]:
        frotas_ordenadas = sorted(g['frotas'].items(), key=lambda kv: kv[0])
        frotas_com_anomalia.append({
            'anomalia': g['anomalia'],
            'qtd': g['qtd'],
            'frotas': [{'codigo': cod, 'compartimentos': sorted(comps)} for cod, comps in frotas_ordenadas]
        })

    prioridades = sorted(criticas, key=lambda i: i['data_coleta'] or '', reverse=True)
    for item in prioridades:
        try: item['data_coleta_br'] = datetime.strptime(item['data_coleta'][:10], '%Y-%m-%d').strftime('%d/%m/%Y')
        except Exception: item['data_coleta_br'] = item['data_coleta']

    periodo_label = 'Período completo'
    if data_inicio and data_fim:
        try:
            di = datetime.strptime(data_inicio, '%Y-%m-%d').strftime('%d/%m/%Y')
            df = datetime.strptime(data_fim, '%Y-%m-%d').strftime('%d/%m/%Y')
            periodo_label = f'{di} a {df}'
        except Exception:
            pass

    metrics = {
        'total': total, 'media_dia': media_dia, 'atencao': len(anomalias), 'criticas': len(criticas),
        'taxa_atencao': taxa_atencao, 'taxa_critica': taxa_critica
    }

    return render_template(
        'analises_dashboard_pdf.html',
        metrics=metrics, periodo=periodo_label, gerado_em=datetime.now().strftime('%d/%m/%Y %H:%M'),
        risco_por_componente=risco_por_componente, distribuicao_resultado=distribuicao_resultado,
        pizza_compartimento=pizza_compartimento, pizza_tipo=pizza_tipo,
        frotas_com_anomalia=frotas_com_anomalia, prioridades=prioridades,
        data_inicio=data_inicio, data_fim=data_fim,
        datas_min=datas_validas[0] if datas_validas else '', datas_max=datas_validas[-1] if datas_validas else ''
    )


@app.route('/api/upload_analises', methods=['POST'])
def upload_analises():
    if not session.get('logado'): return {"status": "erro"}, 403
    if 'file' not in request.files: flash('Nenhum ficheiro selecionado.', 'danger'); return redirect(url_for('analises'))

    file = request.files['file']
    filename = file.filename.lower()
    if filename == '': flash('Nenhum ficheiro selecionado.', 'danger'); return redirect(url_for('analises'))

    conn = get_db_connection()
    count_novos = 0
    count_atualizados = 0

    # 1. Busca TUDO que já existe no banco antes de começar a mexer (Para usar na Auto-Cura depois)
    analises_no_banco = conn.execute("SELECT id, id_frota, data_coleta, compartimento, status_tratativa, diagnostico FROM analises_oleo").fetchall()

    # Usamos um SET (conjunto) para anotar as chaves lidas hoje sem correr risco de duplicá-las internamente
    chaves_processadas_agora = set()

    # Guarda todas as datas de coleta válidas (YYYY-MM-DD) vistas no arquivo, para saber
    # qual JANELA de tempo esse upload realmente cobre — o export do CHB às vezes traz só
    # a última semana, às vezes um mês inteiro, então isso varia a cada envio.
    datas_validas_arquivo = set()

    try:
        import unicodedata

        def norm_txt(valor):
            txt = str(valor or '').strip().upper()
            txt = unicodedata.normalize('NFKD', txt)
            txt = ''.join(ch for ch in txt if not unicodedata.combining(ch))
            txt = ' '.join(txt.split())
            return txt

        lines_data = []
        if filename.endswith('.xlsx'):
            import pandas as pd
            df = pd.read_excel(file, header=None)
            for _, row in df.iterrows():
                row_vals = [str(cell).strip() if not pd.isna(cell) else '' for cell in row.values]
                lines_data.append(row_vals)
        else:
            content_bytes = file.read()
            try: content_str = content_bytes.decode('utf-8-sig')
            except: content_str = content_bytes.decode('latin1', errors='ignore')
            lines = content_str.splitlines()
            reader = csv.reader(lines, delimiter=';' if lines and ';' in lines[0] and ',' not in lines[0] else ',')
            for row in reader: lines_data.append([c.strip() for c in row])

        header_map = None
        for row in lines_data:
            if not row: continue
            row_norm = [norm_txt(c) for c in row]

            if header_map is None:
                tem_frota = any(v in ['VEICULO', 'FROTA'] for v in row_norm)
                tem_comp = any('COMPARTIMENTO' in v for v in row_norm)
                if tem_frota and tem_comp:
                    def idx_exato(opcoes, prefer_last=False):
                        indices = [i for i, v in enumerate(row_norm) if v in opcoes]
                        if not indices:
                            return None
                        return indices[-1] if prefer_last else indices[0]

                    def idx_contem(texto):
                        for i, v in enumerate(row_norm):
                            if texto in v:
                                return i
                        return None

                    idx_frota = idx_exato(['VEICULO', 'FROTA'])
                    idx_comp = idx_exato(['COMPARTIMENTO'])
                    idx_data = idx_exato(['DATA DA COLETA', 'DATA COLETA'])
                    if idx_data is None:
                        idx_data = idx_contem('DATA COLETA')
                    if idx_data is None:
                        idx_data = idx_exato(['DATA'])
                    if idx_data is None:
                        idx_data = idx_contem('DATA')

                    idx_parecer = idx_exato(['OBSERVACAO', 'PARECER', 'RECOMENDACAO'])
                    idx_diag = idx_exato(['DIAGNOSTICO', 'RESULTADO', 'CLASSIFICACAO'])
                    if idx_diag is None:
                        # Em layouts novos, o diagnóstico costuma vir na última coluna "Descrição"
                        idx_diag = idx_exato(['DESCRICAO'], prefer_last=True)

                    if idx_frota is None or idx_comp is None or idx_data is None:
                        continue

                    header_map = {
                        'frota': idx_frota,
                        'data': idx_data,
                        'comp': idx_comp,
                        'parecer': idx_parecer,
                        'diag': idx_diag
                    }
                continue

            # Extração de dados (Limpando espaços extras para evitar erros)
            if len(row) <= header_map['frota'] or len(row) <= header_map['data'] or len(row) <= header_map['comp']:
                continue

            frota = str(row[header_map['frota']]).upper().replace('.0', '').strip()
            data_val = str(row[header_map['data']]).strip()

            try:
                if len(data_val) >= 10:
                    if '/' in data_val: parsed_date = datetime.strptime(data_val[:10], '%d/%m/%Y').strftime('%Y-%m-%d')
                    elif '-' in data_val: parsed_date = datetime.strptime(data_val[:10], '%Y-%m-%d').strftime('%Y-%m-%d')
                    else: parsed_date = data_val
                else: parsed_date = data_val
            except: parsed_date = data_val

            if re.match(r'^\d{4}-\d{2}-\d{2}$', parsed_date):
                datas_validas_arquivo.add(parsed_date)

            # Normaliza maiúsculas/espaços para que "200 - Carter Motor" e "200 - CARTER MOTOR"
            # sejam sempre a MESMA chave — evita duplicatas escaparem da constraint UNIQUE
            # só por causa de uma formatação diferente entre exportações do CHB.
            comp_base = ' '.join(str(row[header_map['comp']]).strip().upper().split())
            comp_desc = ' '.join(str(row[header_map['comp'] + 1]).strip().upper().split()) if len(row) > header_map['comp'] + 1 else ''
            comp = f"{comp_base} - {comp_desc}" if comp_desc else comp_base

            diag = str(row[header_map['diag']]).strip() if header_map.get('diag') is not None and len(row) > header_map['diag'] else ""
            parecer = str(row[header_map['parecer']]).strip() if header_map.get('parecer') is not None and len(row) > header_map['parecer'] else ''

            chave_atual = f"{frota}_{parsed_date}_{comp}"
            chaves_processadas_agora.add(chave_atual)

            # =========================================================================
            # LÓGICA BLINDADA: Busca direto no banco a cada linha.
            # Isso mata o erro UNIQUE, pois se houver linhas repetidas na planilha,
            # a segunda linha vai bater aqui, ver que a primeira já entrou, e fará um UPDATE!
            # =========================================================================
            existente = conn.execute("SELECT id FROM analises_oleo WHERE id_frota=? AND data_coleta=? AND compartimento=?", (frota, parsed_date, comp)).fetchone()

            if existente:
                conn.execute("UPDATE analises_oleo SET diagnostico=?, parecer=? WHERE id=?",
                             (diag, parecer, existente['id']))
                count_atualizados += 1
            else:
                try:
                    conn.execute("INSERT INTO analises_oleo (id_frota, data_coleta, compartimento, diagnostico, parecer) VALUES (?, ?, ?, ?, ?)",
                                 (frota, parsed_date, comp, diag, parecer))
                    count_novos += 1
                except sqlite3.IntegrityError:
                    pass # Proteção extra invisível

        # 3. AUTO-CURA REFINADA:
        # Pega a foto do banco tirada no passo 1 e compara com o que lemos do Excel.
        # Se sumiu do Excel, não está 'CONCLUÍDO' E NÃO É CRÍTICA, a gente apaga.
        # Análises críticas/anômalas NUNCA são removidas automaticamente — só saem do
        # relatório quando alguém registra a tratativa (status_tratativa = 'CONCLUÍDO').
        #
        # IMPORTANTE: o export do CHB não tem uma janela de datas fixa (já vimos arquivos
        # cobrindo só a última semana e outros cobrindo um mês inteiro). Por isso só
        # "sana" um registro se a DATA DELE está dentro do período que ESTE arquivo
        # realmente cobre — do contrário, um upload de janela curta apagaria em massa
        # pendências antigas e válidas só porque elas não cabiam nesse recorte específico.
        data_min_arquivo = min(datas_validas_arquivo) if datas_validas_arquivo else None
        data_max_arquivo = max(datas_validas_arquivo) if datas_validas_arquivo else None

        removidos = 0
        for row_db in analises_no_banco:
            chave_db = f"{row_db['id_frota']}_{row_db['data_coleta']}_{row_db['compartimento']}"
            ja_lida_de_novo = chave_db in chaves_processadas_agora
            ja_concluida = row_db['status_tratativa'] == 'CONCLUÍDO'
            eh_critica = _eh_diagnostico_critico(row_db['diagnostico'])
            dentro_da_janela_do_arquivo = (
                data_min_arquivo is not None
                and data_min_arquivo <= (row_db['data_coleta'] or '') <= data_max_arquivo
            )
            if dentro_da_janela_do_arquivo and not ja_lida_de_novo and not ja_concluida and not eh_critica:
                conn.execute("DELETE FROM analises_oleo WHERE id = ?", (row_db['id'],))
                removidos += 1

        conn.commit()
        periodo_txt = f' (arquivo cobre {data_min_arquivo} a {data_max_arquivo})' if data_min_arquivo else ''
        flash(f'Sincronização OK! {count_novos} novas, {count_atualizados} atualizações/repetidas e {removidos} removidas (sanadas){periodo_txt}. Análises críticas nunca são removidas automaticamente.', 'success')

    except Exception as e:
        conn.rollback() # Se der qualquer outro erro fatal, desfaz tudo para não quebrar o banco
        flash(f'Erro no processamento: {str(e)}', 'danger')
    finally:
        conn.close()

    return redirect(url_for('analises'))
def _parse_lista_frotas(frotas_raw):
    return sorted(set(
        re.sub(r'[^A-Z0-9\-]', '', f.strip().upper())
        for f in re.split(r'[,;\n\r\t ]+', frotas_raw)
        if f.strip()
    ))


@app.route('/api/salvar_grupo_responsavel', methods=['POST'])
def salvar_grupo_responsavel():
    if not session.get('logado'): return redirect(url_for('login'))
    nome_grupo = request.form.get('nome_grupo', '').strip().upper()
    responsavel = request.form.get('responsavel', '').strip().upper()
    frotas_raw = request.form.get('frotas', '')

    if not nome_grupo or not responsavel:
        flash('Informe o nome do grupo e o responsável.', 'danger')
        return redirect(url_for('analises', aba='responsaveis'))

    conn = get_db_connection()
    existente = conn.execute('SELECT id FROM grupos_responsaveis WHERE nome_grupo=?', (nome_grupo,)).fetchone()
    if existente:
        conn.close()
        flash(f'Já existe um grupo chamado "{nome_grupo}". Escolha outro nome.', 'danger')
        return redirect(url_for('analises', aba='responsaveis'))

    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor = conn.execute('INSERT INTO grupos_responsaveis (nome_grupo, responsavel_nome, criado_em) VALUES (?, ?, ?)',
                          (nome_grupo, responsavel, ts))
    grupo_id = cursor.lastrowid

    frotas = _parse_lista_frotas(frotas_raw)
    for frota in frotas:
        conn.execute(
            'INSERT INTO grupo_frotas (grupo_id, id_frota) VALUES (?, ?) '
            'ON CONFLICT(id_frota) DO UPDATE SET grupo_id = excluded.grupo_id',
            (grupo_id, frota)
        )
    conn.commit(); conn.close()
    flash(f'Grupo "{nome_grupo}" criado com sucesso, com {len(frotas)} frota(s)!', 'success')
    return redirect(url_for('analises', aba='responsaveis'))


@app.route('/api/excluir_grupo_responsavel/<int:id_grupo>')
def excluir_grupo_responsavel(id_grupo):
    if not session.get('logado'): return redirect(url_for('login'))
    conn = get_db_connection()
    conn.execute('DELETE FROM grupo_frotas WHERE grupo_id = ?', (id_grupo,))
    conn.execute('DELETE FROM grupos_responsaveis WHERE id = ?', (id_grupo,))
    conn.commit(); conn.close()
    flash('Grupo removido com sucesso!', 'danger')
    return redirect(url_for('analises', aba='responsaveis'))


@app.route('/api/adicionar_frotas_grupo', methods=['POST'])
def adicionar_frotas_grupo():
    if not session.get('logado'): return redirect(url_for('login'))
    grupo_id = request.form.get('grupo_id', '').strip()
    frotas_raw = request.form.get('frotas', '')
    if not grupo_id:
        flash('Grupo inválido.', 'danger')
        return redirect(url_for('analises', aba='responsaveis'))

    frotas = _parse_lista_frotas(frotas_raw)
    if not frotas:
        flash('Cole ao menos uma frota para adicionar.', 'danger')
        return redirect(url_for('analises', aba='responsaveis'))

    conn = get_db_connection()
    for frota in frotas:
        # UNIQUE em id_frota: uma frota que já pertencia a outro grupo passa para este.
        conn.execute(
            'INSERT INTO grupo_frotas (grupo_id, id_frota) VALUES (?, ?) '
            'ON CONFLICT(id_frota) DO UPDATE SET grupo_id = excluded.grupo_id',
            (int(grupo_id), frota)
        )
    conn.commit(); conn.close()
    flash(f'{len(frotas)} frota(s) adicionada(s) ao grupo!', 'success')
    return redirect(url_for('analises', aba='responsaveis'))


@app.route('/api/remover_frota_grupo/<int:id_reg>')
def remover_frota_grupo(id_reg):
    if not session.get('logado'): return redirect(url_for('login'))
    conn = get_db_connection()
    conn.execute('DELETE FROM grupo_frotas WHERE id = ?', (id_reg,))
    conn.commit(); conn.close()
    flash('Frota removida do grupo com sucesso!', 'danger')
    return redirect(url_for('analises', aba='responsaveis'))

@app.route('/api/salvar_tratativa', methods=['POST'])
def salvar_tratativa():
    if not session.get('logado'): return jsonify({"status": "erro"}), 403
    dados = request.get_json()
    id_analise = dados.get('id_analise')
    tratativa = dados.get('tratativa')
    status = dados.get('status')
    # Permite escolher a data em que a tratativa foi de fato realizada (ex: registrando um
    # atraso), em vez de forçar sempre a data de hoje. Sem valor informado, cai no padrão de hoje.
    data_tratativa = (dados.get('data_tratativa') or '').strip() or datetime.now().strftime('%Y-%m-%d')

    conn = get_db_connection()
    conn.execute('UPDATE analises_oleo SET tratativa = ?, status_tratativa = ?, data_tratativa = ? WHERE id = ?', (tratativa, status, data_tratativa, id_analise))
    conn.commit(); conn.close()
    return jsonify({"status": "sucesso"})

@app.route('/api/excluir_analise/<int:id_analise>')
def excluir_analise(id_analise):
    if not session.get('logado'): return redirect(url_for('login'))

    conn = get_db_connection()
    conn.execute('DELETE FROM analises_oleo WHERE id = ?', (id_analise,))
    conn.commit()
    conn.close()

    flash('Análise removida do painel com sucesso!', 'danger')
    return redirect(url_for('analises'))

@app.route('/api/excluir_todas_analises', methods=['POST'])
def excluir_todas_analises():
    if not session.get('logado'): return redirect(url_for('login'))

    conn = get_db_connection()
    # Apaga absolutamente todas as linhas da tabela de análises
    conn.execute('DELETE FROM analises_oleo')
    conn.commit()
    conn.close()

    flash('Atenção: Todas as análises foram excluídas do painel!', 'danger')
    return redirect(url_for('analises'))

# ==========================================
# MÓDULO: PROGRAMAÇÃO DE REFORMA DE MÁQUINAS
# ==========================================

@app.route('/reforma')
def reforma():
    if not session.get('logado'): return redirect(url_for('login'))
    if not is_admin_session():
        flash('Seu perfil não tem permissão para acessar Reforma de Máquinas.', 'danger')
        return redirect(url_for('admin'))

    from datetime import date
    hoje = date.today()
    conn = get_db_connection()
    primeiro_mes = conn.execute(
        'SELECT ano, mes FROM reforma_programacao ORDER BY ano, mes LIMIT 1'
    ).fetchone()
    if primeiro_mes:
        inicio_grade = date(int(primeiro_mes['ano']), int(primeiro_mes['mes']), 1)
    else:
        inicio_grade = date(hoje.year, hoje.month, 1)

    meses_grade = []
    for i in range(12):
        mes = (inicio_grade.month - 1 + i) % 12 + 1
        ano = inicio_grade.year + ((inicio_grade.month - 1 + i) // 12)
        meses_grade.append({
            'ano': ano, 'mes': mes,
            'label': f"{['Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez'][mes-1]}/{str(ano)[2:]}"
        })

    frotas_reforma = conn.execute('''
        SELECT f.id_frota,
               COALESCE(NULLIF(TRIM(f.descricao), ''), 'SEM DESCRIÇÃO') as descricao,
               COALESCE(NULLIF(TRIM(f.agrupamento), ''), 'GERAL') as agrupamento,
               COALESCE(NULLIF(TRIM(f.especialidade), ''), 'OUTROS') as especialidade,
               COALESCE(rm.ano_fabricacao, '') as ano_fabricacao,
               COALESCE(rm.km_horimetro, '') as km_horimetro,
               COALESCE(rm.dt_ultima_reforma, '') as dt_ultima_reforma,
               COALESCE(rm.observacao, '') as observacao
        FROM frotas f
        INNER JOIN reforma_maquinas rm ON f.id_frota = rm.id_frota
        WHERE EXISTS (
            SELECT 1 FROM reforma_programacao rp WHERE rp.id_frota = f.id_frota
        )
        ORDER BY f.agrupamento, f.especialidade, f.id_frota
    ''').fetchall()

    programacoes_raw = conn.execute(
        'SELECT id_frota, ano, mes, status, observacao FROM reforma_programacao'
    ).fetchall()

    prog_dict = {}
    for p in programacoes_raw:
        chave_frota = p['id_frota']
        chave_mes = f"{p['ano']}_{p['mes']}"
        if chave_frota not in prog_dict:
            prog_dict[chave_frota] = {}
        prog_dict[chave_frota][chave_mes] = {
            'status': p['status'],
            'observacao': p['observacao'] or ''
        }

    agrupamentos_unicos = conn.execute(
        'SELECT DISTINCT agrupamento FROM frotas WHERE agrupamento IS NOT NULL AND TRIM(agrupamento) != "" ORDER BY agrupamento'
    ).fetchall()
    conn.close()

    return render_template('reforma.html',
        frotas_reforma=[dict(f) for f in frotas_reforma],
        meses_grade=meses_grade,
        prog_dict=prog_dict,
        agrupamentos=[a['agrupamento'] for a in agrupamentos_unicos]
    )


@app.route('/api/reforma/salvar_frota', methods=['POST'])
def reforma_salvar_frota():
    if not session.get('logado'): return jsonify({'status': 'erro'}), 403
    dados = request.get_json(silent=True) or {}
    id_frota = dados.get('id_frota', '').strip().upper()
    if not id_frota: return jsonify({'status': 'erro', 'msg': 'Frota obrigatória'}), 400
    conn = get_db_connection()
    existente = conn.execute('SELECT id FROM reforma_maquinas WHERE id_frota = ?', (id_frota,)).fetchone()
    if existente:
        conn.execute(
            'UPDATE reforma_maquinas SET ano_fabricacao=?, km_horimetro=?, dt_ultima_reforma=?, observacao=? WHERE id_frota=?',
            (dados.get('ano_fabricacao', ''), dados.get('km_horimetro', ''), dados.get('dt_ultima_reforma', ''), dados.get('observacao', ''), id_frota)
        )
    else:
        conn.execute(
            'INSERT INTO reforma_maquinas (id_frota, ano_fabricacao, km_horimetro, dt_ultima_reforma, observacao) VALUES (?,?,?,?,?)',
            (id_frota, dados.get('ano_fabricacao', ''), dados.get('km_horimetro', ''), dados.get('dt_ultima_reforma', ''), dados.get('observacao', ''))
        )
    conn.commit()
    conn.close()
    return jsonify({'status': 'sucesso'})


@app.route('/api/reforma/atualizar_celula', methods=['POST'])
def reforma_atualizar_celula():
    if not session.get('logado'): return jsonify({'status': 'erro'}), 403
    dados = request.get_json(silent=True) or {}
    id_frota = dados.get('id_frota', '').strip().upper()
    ano = int(dados.get('ano', 0))
    mes = int(dados.get('mes', 0))
    status = dados.get('status', '').strip().upper()
    observacao = dados.get('observacao', '').strip()
    if not id_frota or not ano or not mes:
        return jsonify({'status': 'erro', 'msg': 'Dados incompletos'}), 400
    conn = get_db_connection()
    existente_frota = conn.execute('SELECT id FROM reforma_maquinas WHERE id_frota = ?', (id_frota,)).fetchone()
    if not existente_frota:
        conn.execute('INSERT OR IGNORE INTO reforma_maquinas (id_frota) VALUES (?)', (id_frota,))
    if status == 'LIMPAR':
        conn.execute('DELETE FROM reforma_programacao WHERE id_frota=? AND ano=? AND mes=?', (id_frota, ano, mes))
    else:
        conn.execute(
            '''INSERT INTO reforma_programacao (id_frota, ano, mes, status, observacao)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(id_frota, ano, mes) DO UPDATE SET status=excluded.status, observacao=excluded.observacao''',
            (id_frota, ano, mes, status, observacao)
        )
    conn.commit()
    conn.close()
    return jsonify({'status': 'sucesso'})


@app.route('/api/reforma/importar_frotas', methods=['POST'])
def reforma_importar_frotas():
    if not session.get('logado'): return jsonify({'status': 'erro'}), 403
    if not is_admin_session(): return jsonify({'status': 'erro', 'msg': 'Sem permissão'}), 403
    dados = request.get_json(silent=True) or {}
    frotas_lista = dados.get('frotas', [])
    conn = get_db_connection()
    inseridas = 0
    for f in frotas_lista:
        id_frota = str(f.get('id_frota', '') or '').strip().upper()
        if not id_frota: continue
        try:
            conn.execute(
                '''INSERT INTO reforma_maquinas (id_frota, ano_fabricacao, km_horimetro, dt_ultima_reforma)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(id_frota) DO UPDATE SET ano_fabricacao=excluded.ano_fabricacao,
                   km_horimetro=excluded.km_horimetro, dt_ultima_reforma=excluded.dt_ultima_reforma''',
                (id_frota, str(f.get('ano_fabricacao', '') or ''), str(f.get('km_horimetro', '') or ''), str(f.get('dt_ultima_reforma', '') or ''))
            )
            inseridas += 1
        except Exception:
            pass
    conn.commit()
    conn.close()
    return jsonify({'status': 'sucesso', 'inseridas': inseridas})


@app.route('/api/reforma/upload_planilha', methods=['POST'])
def reforma_upload_planilha():
    if not session.get('logado'): return jsonify({'status': 'erro'}), 403
    if not is_admin_session(): return jsonify({'status': 'erro', 'msg': 'Sem permissão'}), 403
    if 'arquivo' not in request.files:
        return jsonify({'status': 'erro', 'msg': 'Nenhum arquivo enviado'}), 400
    arquivo = request.files['arquivo']
    if not arquivo.filename.lower().endswith('.xlsx'):
        return jsonify({'status': 'erro', 'msg': 'Arquivo deve ser .xlsx'}), 400
    import tempfile, sys as _sys
    if BASE_DIR not in _sys.path:
        _sys.path.insert(0, BASE_DIR)
    from importar_reforma_planilha import (
        WorkbookReader, import_base_frota, import_planning,
        import_os as planilha_import_os, import_gastos, import_orcamentos
    )
    tmp = tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False)
    arquivo.save(tmp.name)
    tmp.close()
    try:
        wb = WorkbookReader(tmp.name)
        conn = get_db_connection()
        cur = conn.execute(
            'INSERT INTO reforma_importacoes (arquivo, data_importacao, observacao) VALUES (?,?,?)',
            (arquivo.filename, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'Upload via web')
        )
        import_id = cur.lastrowid
        base = import_base_frota(conn, wb)
        planning = import_planning(conn, wb, import_id)
        os_cnt = planilha_import_os(conn, wb, import_id)
        gastos = import_gastos(conn, wb, import_id)
        orcamentos = import_orcamentos(conn, wb, import_id)
        conn.execute(
            'UPDATE reforma_importacoes SET total_abas=?, total_linhas=?, observacao=? WHERE id=?',
            (len(wb.sheets), planning + os_cnt + gastos + orcamentos,
             f'base={base}; planejamento={planning}; os={os_cnt}; gastos={gastos}; orcamentos={orcamentos}',
             import_id)
        )
        conn.commit()
        conn.close()
        return jsonify({'status': 'sucesso', 'import_id': import_id, 'base_frota': base,
                        'planejamento': planning, 'os': os_cnt, 'gastos': gastos, 'orcamentos': orcamentos})
    except Exception as e:
        return jsonify({'status': 'erro', 'msg': str(e)}), 500
    finally:
        try: os.unlink(tmp.name)
        except: pass


@app.route('/api/reforma/resumo_data')
def reforma_resumo_data():
    if not session.get('logado'): return jsonify([])
    conn = get_db_connection()
    rows = conn.execute('''
        SELECT especialidade, agrupamento, ano, mes, status, COUNT(*) as total
        FROM reforma_planejamento_planilha
        WHERE importacao_id = (SELECT MAX(id) FROM reforma_importacoes)
        GROUP BY especialidade, agrupamento, ano, mes, status
        ORDER BY agrupamento, especialidade, ano, mes
    ''').fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/api/reforma/orcamento_data')
def reforma_orcamento_data():
    if not session.get('logado'): return jsonify([])
    conn = get_db_connection()
    rows = conn.execute('''
        SELECT o.id_frota, o.modelo, o.descricao_frota, o.ano_fabricacao,
               o.especialidade, o.agrupamento, o.componente, o.valor, o.aba
        FROM reforma_orcamento_planilha o
        WHERE o.importacao_id = (SELECT MAX(id) FROM reforma_importacoes)
        ORDER BY o.agrupamento, o.especialidade, o.id_frota, o.componente
    ''').fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/api/reforma/os_data')
def reforma_os_data():
    if not session.get('logado'): return jsonify([])
    conn = get_db_connection()
    rows = conn.execute('''
        SELECT documento, id_frota, empresa, status_os, data_abertura,
               data_liberacao, tipo_manutencao, centro_custo, especialidade,
               agrupamento, descricao_frota
        FROM reforma_os_planilha
        WHERE importacao_id = (SELECT MAX(id) FROM reforma_importacoes)
        ORDER BY agrupamento, especialidade, id_frota
    ''').fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/api/reforma/gastos_data')
def reforma_gastos_data():
    if not session.get('logado'): return jsonify([])
    conn = get_db_connection()
    rows = conn.execute('''
        SELECT id_frota, descricao_frota, empresa, ano, mes, especialidade, valor, aba
        FROM reforma_gastos_planilha
        WHERE importacao_id = (SELECT MAX(id) FROM reforma_importacoes)
        ORDER BY aba, id_frota, ano, mes
    ''').fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/api/reforma/importacoes')
def reforma_lista_importacoes():
    if not session.get('logado'): return jsonify([])
    conn = get_db_connection()
    rows = conn.execute('''
        SELECT id, arquivo, data_importacao, total_abas, total_linhas, observacao
        FROM reforma_importacoes ORDER BY id DESC LIMIT 10
    ''').fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


# ── Filtros por Frota ─────────────────────────────────────────────────────────

@app.route('/api/ferramentaria/filtros/buscar')
def filtros_buscar_frota():
    if not session.get('logado'): return jsonify({'erro': 'Não autenticado'}), 401
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify({'erro': 'Informe o número ou nome da frota.'})
    conn = get_db_connection()
    # Tenta buscar pelo id_frota exato primeiro
    frota_row = conn.execute(
        'SELECT id_frota, descricao, especialidade, agrupamento FROM frotas WHERE UPPER(TRIM(id_frota)) = ?',
        (q.upper(),)
    ).fetchone()
    # Se não achou, busca por descrição
    if not frota_row:
        frota_row = conn.execute(
            'SELECT id_frota, descricao, especialidade, agrupamento FROM frotas WHERE descricao LIKE ? LIMIT 1',
            (f'%{q.upper()}%',)
        ).fetchone()
    if not frota_row:
        conn.close()
        return jsonify({'erro': f'Frota "{q}" não encontrada no cadastro.'})

    id_frota = str(frota_row['id_frota'])
    frota_info = dict(frota_row)

    # Busca modelo da frota
    modelo_row = conn.execute(
        'SELECT modelo, especialidade FROM frotas_modelos WHERE id_frota = ?', (id_frota,)
    ).fetchone()

    if not modelo_row:
        conn.close()
        return jsonify({
            'frota': frota_info,
            'modelo': None,
            'irmas': [],
            'filtros': [],
            'aviso': 'Esta frota não tem modelo de filtros cadastrado.'
        })

    modelo = modelo_row['modelo']
    especialidade = modelo_row['especialidade']

    # Frotas irmãs (mesmo modelo, excluindo a pesquisada)
    irmas = [dict(r) for r in conn.execute(
        '''SELECT fm.id_frota, COALESCE(f.descricao, fm.id_frota) as descricao
           FROM frotas_modelos fm
           LEFT JOIN frotas f ON f.id_frota = fm.id_frota
           WHERE fm.modelo = ? AND fm.id_frota != ?
           ORDER BY fm.id_frota''',
        (modelo, id_frota)
    ).fetchall()]

    # Filtros do modelo com estoque
    filtros = [dict(r) for r in conn.execute(
        '''SELECT fc.id, fc.tipo_filtro, fc.codigo_filtro,
                  COALESCE(ff.descricao, '') as descricao_item
           FROM filtros_catalogo fc
           LEFT JOIN ferramentaria_ferramentas ff ON ff.codigo = fc.codigo_filtro AND ff.ativo = 1
           WHERE fc.modelo = ?
           ORDER BY fc.tipo_filtro, fc.codigo_filtro''',
        (modelo,)
    ).fetchall()]

    conn.close()
    return jsonify({
        'frota': frota_info,
        'modelo': modelo,
        'especialidade': especialidade,
        'irmas': irmas,
        'filtros': filtros
    })


@app.route('/api/ferramentaria/filtros/item/salvar', methods=['POST'])
def filtros_salvar_item():
    if not session.get('logado'): return jsonify({'status': 'erro', 'msg': 'Não autenticado'}), 401
    if not is_admin_session(): return jsonify({'status': 'erro', 'msg': 'Sem permissão'}), 403
    id_item = request.form.get('id', '').strip()
    modelo = request.form.get('modelo', '').strip()
    codigo = request.form.get('codigo_filtro', '').strip()
    tipo = request.form.get('tipo_filtro', '').strip().upper()
    if not modelo or not codigo or not tipo:
        return jsonify({'status': 'erro', 'msg': 'Modelo, código e tipo são obrigatórios.'})
    conn = get_db_connection()
    try:
        if id_item:
            conn.execute(
                'UPDATE filtros_catalogo SET codigo_filtro=?, tipo_filtro=? WHERE id=? AND modelo=?',
                (codigo, tipo, int(id_item), modelo)
            )
        else:
            conn.execute(
                'INSERT OR REPLACE INTO filtros_catalogo (modelo, codigo_filtro, tipo_filtro) VALUES (?,?,?)',
                (modelo, codigo, tipo)
            )
        conn.commit()
        conn.close()
        return jsonify({'status': 'ok'})
    except Exception as e:
        conn.close()
        return jsonify({'status': 'erro', 'msg': str(e)})


@app.route('/api/ferramentaria/filtros/item/excluir/<int:id_item>', methods=['POST'])
def filtros_excluir_item(id_item):
    if not session.get('logado'): return jsonify({'status': 'erro', 'msg': 'Não autenticado'}), 401
    if not is_admin_session(): return jsonify({'status': 'erro', 'msg': 'Sem permissão'}), 403
    conn = get_db_connection()
    conn.execute('DELETE FROM filtros_catalogo WHERE id = ?', (id_item,))
    conn.commit()
    conn.close()
    return jsonify({'status': 'ok'})


@app.route('/api/ferramentaria/filtros/frota/modelo/salvar', methods=['POST'])
def filtros_salvar_modelo_frota():
    if not session.get('logado'): return jsonify({'status': 'erro', 'msg': 'Não autenticado'}), 401
    if not is_admin_session(): return jsonify({'status': 'erro', 'msg': 'Sem permissão'}), 403
    id_frota = request.form.get('id_frota', '').strip()
    modelo = request.form.get('modelo', '').strip()
    especialidade = request.form.get('especialidade', '').strip().upper()
    if not id_frota or not modelo:
        return jsonify({'status': 'erro', 'msg': 'Frota e modelo são obrigatórios.'})
    conn = get_db_connection()
    conn.execute(
        'INSERT OR REPLACE INTO frotas_modelos (id_frota, modelo, especialidade) VALUES (?,?,?)',
        (id_frota, modelo, especialidade)
    )
    conn.commit()
    conn.close()
    return jsonify({'status': 'ok'})


# ── Agregados (peças em fornecedores) ─────────────────────────────────────────

UPLOAD_AGREGADOS_DIR = os.path.join(BASE_DIR, 'uploads', 'agregados')
EXTENSOES_IMAGEM_PERMITIDAS = {'png', 'jpg', 'jpeg', 'webp', 'gif'}

def _extensao_imagem_permitida(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in EXTENSOES_IMAGEM_PERMITIDAS


def _salvar_imagem_comprimida(arquivo_upload, caminho_sem_extensao, max_lado=1280, qualidade=78):
    """Redimensiona e recomprime a imagem (sempre como JPEG) antes de gravar no disco.
    Uma foto de celular direto (3-8 MB) sai daqui com ~100-300 KB, sem perda visível
    para fins de identificação da peça — essencial para não estourar o disco (bem
    limitado) do PythonAnywhere à medida que o número de fotos crescer."""
    img = Image.open(arquivo_upload)
    img = ImageOps.exif_transpose(img)
    if img.mode in ('RGBA', 'LA', 'P'):
        img = img.convert('RGBA')
        fundo = Image.new('RGB', img.size, (255, 255, 255))
        fundo.paste(img, mask=img.split()[-1])
        img = fundo
    else:
        img = img.convert('RGB')
    img.thumbnail((max_lado, max_lado), Image.LANCZOS)
    caminho_final = caminho_sem_extensao + '.jpg'
    img.save(caminho_final, 'JPEG', quality=qualidade, optimize=True)
    return os.path.basename(caminho_final)


@app.route('/ferramentaria/agregados/imagem/<path:filename>')
def servir_imagem_agregado(filename):
    if not session.get('logado'): return redirect(url_for('login'))
    filepath = os.path.join(UPLOAD_AGREGADOS_DIR, secure_filename(filename))
    if not os.path.exists(filepath):
        return 'Imagem não encontrada', 404
    return send_file(filepath)


@app.route('/api/ferramentaria/agregados/salvar', methods=['POST'])
def agregados_salvar():
    if not session.get('logado'): return jsonify({'status': 'erro', 'msg': 'Não autenticado'}), 401
    if not (is_admin_session() or tem_acesso_modulo('ferramentaria')):
        return jsonify({'status': 'erro', 'msg': 'Sem permissão'}), 403
    id_item = request.form.get('id', '').strip()
    descricao = request.form.get('descricao', '').strip().upper()
    categoria = request.form.get('categoria', '').strip().upper()
    codigo_novo = request.form.get('codigo_novo', '').strip()
    codigo_recond = request.form.get('codigo_recond', '').strip()
    referencia = request.form.get('referencia', '').strip()
    versao = request.form.get('versao', '').strip()
    modelo_maquina = request.form.get('modelo_maquina', 'CH570').strip().upper()
    remover_imagem = request.form.get('remover_imagem', '').strip() == '1'
    def _ii(k):
        try: return int(request.form.get(k, 0) or 0)
        except: return 0
    saldo_novo = _ii('saldo_novo')
    p_conserto = _ii('p_conserto')
    saldo_recond = _ii('saldo_recond')
    em_manut = _ii('em_manut')
    devendo = _ii('devendo')
    if not descricao:
        return jsonify({'status': 'erro', 'msg': 'Descrição obrigatória.'})
    conn = get_db_connection()
    try:
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        imagem_atual = None
        if id_item:
            row_atual = conn.execute('SELECT imagem FROM ferramentaria_agregados WHERE id=?', (int(id_item),)).fetchone()
            if row_atual: imagem_atual = row_atual['imagem']

        nome_imagem = imagem_atual
        arquivo = request.files.get('imagem')
        if arquivo and arquivo.filename:
            if not _extensao_imagem_permitida(arquivo.filename):
                conn.close()
                return jsonify({'status': 'erro', 'msg': 'Formato de imagem não suportado (use PNG, JPG, WEBP ou GIF).'})
            os.makedirs(UPLOAD_AGREGADOS_DIR, exist_ok=True)
            if imagem_atual:
                antigo = os.path.join(UPLOAD_AGREGADOS_DIR, imagem_atual)
                if os.path.exists(antigo): os.remove(antigo)
            base_nome = f"agregado_{id_item or 'novo'}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
            try:
                nome_imagem = _salvar_imagem_comprimida(arquivo, os.path.join(UPLOAD_AGREGADOS_DIR, secure_filename(base_nome)))
            except Exception:
                conn.close()
                return jsonify({'status': 'erro', 'msg': 'Não foi possível processar a imagem enviada.'})
        elif remover_imagem and imagem_atual:
            antigo = os.path.join(UPLOAD_AGREGADOS_DIR, imagem_atual)
            if os.path.exists(antigo): os.remove(antigo)
            nome_imagem = None

        if id_item:
            conn.execute('''UPDATE ferramentaria_agregados SET
                descricao=?, categoria=?, codigo_novo=?, codigo_recond=?, referencia=?, versao=?,
                saldo_novo=?, p_conserto=?, saldo_recond=?, em_manut=?, devendo=?,
                modelo_maquina=?, imagem=?, atualizado_em=?
                WHERE id=?''',
                (descricao, categoria, codigo_novo, codigo_recond, referencia, versao,
                 saldo_novo, p_conserto, saldo_recond, em_manut, devendo,
                 modelo_maquina, nome_imagem, ts, int(id_item)))
        else:
            cursor_novo = conn.execute('''INSERT INTO ferramentaria_agregados
                (descricao, categoria, codigo_novo, codigo_recond, referencia, versao,
                 saldo_novo, p_conserto, saldo_recond, em_manut, devendo, modelo_maquina, imagem, atualizado_em)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (descricao, categoria, codigo_novo, codigo_recond, referencia, versao,
                 saldo_novo, p_conserto, saldo_recond, em_manut, devendo, modelo_maquina, nome_imagem, ts))
            # Renomeia a imagem do item novo para usar o ID real gerado, mantendo rastreabilidade
            if nome_imagem and 'agregado_novo_' in nome_imagem:
                novo_id = cursor_novo.lastrowid
                nome_final = nome_imagem.replace('agregado_novo_', f'agregado_{novo_id}_')
                os.rename(os.path.join(UPLOAD_AGREGADOS_DIR, nome_imagem), os.path.join(UPLOAD_AGREGADOS_DIR, nome_final))
                conn.execute('UPDATE ferramentaria_agregados SET imagem=? WHERE id=?', (nome_final, novo_id))
        conn.commit()
        conn.close()
        return jsonify({'status': 'ok'})
    except Exception as e:
        conn.close()
        return jsonify({'status': 'erro', 'msg': str(e)})


@app.route('/api/ferramentaria/agregados/excluir/<int:id_item>', methods=['POST'])
def agregados_excluir(id_item):
    if not session.get('logado'): return jsonify({'status': 'erro', 'msg': 'Não autenticado'}), 401
    if not is_admin_session(): return jsonify({'status': 'erro', 'msg': 'Sem permissão'}), 403
    conn = get_db_connection()
    conn.execute('UPDATE ferramentaria_agregados SET ativo=0 WHERE id=?', (id_item,))
    conn.commit()
    conn.close()
    return jsonify({'status': 'ok'})


@app.route('/api/ferramentaria/agregados/<int:id_item>/registros')
def agregados_registros_listar(id_item):
    if not session.get('logado'): return jsonify({'status': 'erro', 'msg': 'Não autenticado'}), 401
    if not (is_admin_session() or tem_acesso_modulo('ferramentaria')):
        return jsonify({'status': 'erro', 'msg': 'Sem permissão'}), 403
    conn = get_db_connection()
    rows = conn.execute('''
        SELECT id, numero_fogo, frota, vida_equipamento, status, observacoes, data_registro, usuario, imagem
        FROM agregados_registros
        WHERE agregado_id=?
        ORDER BY data_registro DESC
    ''', (id_item,)).fetchall()
    conn.close()
    return jsonify({'status': 'ok', 'registros': [dict(r) for r in rows]})


@app.route('/api/ferramentaria/agregados/registrar', methods=['POST'])
def agregados_registrar():
    if not session.get('logado'): return jsonify({'status': 'erro', 'msg': 'Não autenticado'}), 401
    if not (is_admin_session() or tem_acesso_modulo('ferramentaria')):
        return jsonify({'status': 'erro', 'msg': 'Sem permissão'}), 403
    agregado_id = request.form.get('agregado_id', '').strip()
    numero_fogo = request.form.get('numero_fogo', '').strip().upper()
    frota = request.form.get('frota', '').strip().upper()
    vida_equipamento = request.form.get('vida_equipamento', '').strip()
    status_unit = request.form.get('status', 'EM ESTOQUE').strip().upper() or 'EM ESTOQUE'
    observacoes = request.form.get('observacoes', '').strip()
    if not agregado_id:
        return jsonify({'status': 'erro', 'msg': 'ID do agregado obrigatório.'})
    if not numero_fogo and not frota and not vida_equipamento:
        return jsonify({'status': 'erro', 'msg': 'Preencha ao menos um campo (Nº Fogo, Frota ou Vida).'})
    usuario = session.get('perfil_nome', 'Usuário')
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn = get_db_connection()
    try:
        cursor_novo = conn.execute('''INSERT INTO agregados_registros
            (agregado_id, numero_fogo, frota, vida_equipamento, status, observacoes, data_registro, usuario)
            VALUES (?,?,?,?,?,?,?,?)''',
            (int(agregado_id), numero_fogo, frota, vida_equipamento, status_unit, observacoes, ts, usuario))
        novo_id = cursor_novo.lastrowid

        arquivo = request.files.get('imagem')
        if arquivo and arquivo.filename:
            if not _extensao_imagem_permitida(arquivo.filename):
                conn.close()
                return jsonify({'status': 'erro', 'msg': 'Formato de imagem não suportado (use PNG, JPG, WEBP ou GIF).'})
            os.makedirs(UPLOAD_AGREGADOS_DIR, exist_ok=True)
            base_nome = f"unidade_{novo_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
            try:
                nome_imagem = _salvar_imagem_comprimida(arquivo, os.path.join(UPLOAD_AGREGADOS_DIR, secure_filename(base_nome)))
            except Exception:
                conn.close()
                return jsonify({'status': 'erro', 'msg': 'Não foi possível processar a imagem enviada.'})
            conn.execute('UPDATE agregados_registros SET imagem=? WHERE id=?', (nome_imagem, novo_id))

        conn.commit()
        conn.close()
        return jsonify({'status': 'ok'})
    except Exception as e:
        conn.close()
        return jsonify({'status': 'erro', 'msg': str(e)})


@app.route('/api/ferramentaria/agregados/registros/<int:id_reg>/atualizar', methods=['POST'])
def agregados_registro_atualizar(id_reg):
    if not session.get('logado'): return jsonify({'status': 'erro', 'msg': 'Não autenticado'}), 401
    if not (is_admin_session() or tem_acesso_modulo('ferramentaria')):
        return jsonify({'status': 'erro', 'msg': 'Sem permissão'}), 403
    status_val = request.form.get('status', 'EM ESTOQUE').strip().upper() or 'EM ESTOQUE'
    observacoes = request.form.get('observacoes', '').strip()
    remover_imagem = request.form.get('remover_imagem', '').strip() == '1'
    conn = get_db_connection()
    try:
        row_atual = conn.execute('SELECT imagem FROM agregados_registros WHERE id=?', (id_reg,)).fetchone()
        imagem_atual = row_atual['imagem'] if row_atual else None
        nome_imagem = imagem_atual

        arquivo = request.files.get('imagem')
        if arquivo and arquivo.filename:
            if not _extensao_imagem_permitida(arquivo.filename):
                conn.close()
                return jsonify({'status': 'erro', 'msg': 'Formato de imagem não suportado (use PNG, JPG, WEBP ou GIF).'})
            os.makedirs(UPLOAD_AGREGADOS_DIR, exist_ok=True)
            if imagem_atual:
                antigo = os.path.join(UPLOAD_AGREGADOS_DIR, imagem_atual)
                if os.path.exists(antigo): os.remove(antigo)
            base_nome = f"unidade_{id_reg}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
            try:
                nome_imagem = _salvar_imagem_comprimida(arquivo, os.path.join(UPLOAD_AGREGADOS_DIR, secure_filename(base_nome)))
            except Exception:
                conn.close()
                return jsonify({'status': 'erro', 'msg': 'Não foi possível processar a imagem enviada.'})
        elif remover_imagem and imagem_atual:
            antigo = os.path.join(UPLOAD_AGREGADOS_DIR, imagem_atual)
            if os.path.exists(antigo): os.remove(antigo)
            nome_imagem = None

        conn.execute('UPDATE agregados_registros SET status=?, observacoes=?, imagem=? WHERE id=?',
                     (status_val, observacoes, nome_imagem, id_reg))
        conn.commit()
        conn.close()
        return jsonify({'status': 'ok'})
    except Exception as e:
        conn.close()
        return jsonify({'status': 'erro', 'msg': str(e)})


# ── Caixa de Ferramentas ──────────────────────────────────────────────────────

@app.route('/api/ferramentaria/caixas/salvar', methods=['POST'])
def caixa_ferramentas_salvar():
    if not session.get('logado'): return jsonify({'status': 'erro', 'msg': 'Não autenticado'}), 401
    if not (is_admin_session() or tem_acesso_modulo('ferramentaria')):
        return jsonify({'status': 'erro', 'msg': 'Sem permissão'}), 403
    caixa_id = request.form.get('id', '').strip()
    mecanico = request.form.get('mecanico', '').strip().upper()
    observacao = request.form.get('observacao', '').strip()
    codigos = request.form.getlist('codigo[]')
    descricoes = request.form.getlist('descricao[]')
    quantidades = request.form.getlist('quantidade[]')
    valores = request.form.getlist('valor_unitario[]')
    if not mecanico:
        return jsonify({'status': 'erro', 'msg': 'Nome do mecânico é obrigatório.'})
    itens = []
    for cod, desc, qty, val in zip(codigos, descricoes, quantidades, valores):
        desc = desc.strip()
        if not desc:
            continue
        try: qty_f = float(str(qty).replace(',', '.'))
        except: qty_f = 1.0
        try: val_f = float(str(val).replace(',', '.'))
        except: val_f = 0.0
        itens.append((cod.strip(), desc, qty_f, val_f))
    conn = get_db_connection()
    try:
        agora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        usuario = session.get('usuario', '')
        if caixa_id:
            conn.execute('UPDATE caixa_ferramentas SET mecanico=?, observacao=?, atualizado_em=? WHERE id=?',
                         (mecanico, observacao, agora, int(caixa_id)))
            conn.execute('DELETE FROM caixa_ferramentas_itens WHERE caixa_id=?', (int(caixa_id),))
            novo_id = int(caixa_id)
        else:
            cur = conn.execute('INSERT INTO caixa_ferramentas (mecanico, observacao, criado_por, criado_em, atualizado_em) VALUES (?,?,?,?,?)',
                               (mecanico, observacao, usuario, agora, agora))
            novo_id = cur.lastrowid
        if itens:
            conn.executemany('INSERT INTO caixa_ferramentas_itens (caixa_id, codigo, descricao, quantidade, valor_unitario) VALUES (?,?,?,?,?)',
                             [(novo_id, c, d, q, v) for c, d, q, v in itens])
        conn.commit()
        conn.close()
        return jsonify({'status': 'ok', 'id': novo_id})
    except Exception as e:
        conn.close()
        return jsonify({'status': 'erro', 'msg': str(e)})


@app.route('/api/ferramentaria/caixas/<int:caixa_id>/itens')
def caixa_ferramentas_itens(caixa_id):
    if not session.get('logado'): return jsonify({'status': 'erro'}), 401
    conn = get_db_connection()
    caixa = conn.execute('SELECT * FROM caixa_ferramentas WHERE id=?', (caixa_id,)).fetchone()
    itens = conn.execute('SELECT * FROM caixa_ferramentas_itens WHERE caixa_id=? ORDER BY id', (caixa_id,)).fetchall()
    conn.close()
    if not caixa:
        return jsonify({'status': 'erro', 'msg': 'Não encontrado'}), 404
    return jsonify({
        'status': 'ok',
        'caixa': dict(caixa),
        'itens': [dict(i) for i in itens]
    })


@app.route('/api/ferramentaria/caixas/excluir/<int:caixa_id>', methods=['POST'])
def caixa_ferramentas_excluir(caixa_id):
    if not session.get('logado'): return jsonify({'status': 'erro', 'msg': 'Não autenticado'}), 401
    if not is_admin_session():
        return jsonify({'status': 'erro', 'msg': 'Apenas administradores podem excluir.'}), 403
    conn = get_db_connection()
    try:
        conn.execute('DELETE FROM caixa_ferramentas_itens WHERE caixa_id=?', (caixa_id,))
        conn.execute('DELETE FROM caixa_ferramentas WHERE id=?', (caixa_id,))
        conn.commit()
        conn.close()
        return jsonify({'status': 'ok'})
    except Exception as e:
        conn.close()
        return jsonify({'status': 'erro', 'msg': str(e)})


# ── Caminhões Oficina ─────────────────────────────────────────────────────────

@app.route('/api/ferramentaria/caminhoes/salvar', methods=['POST'])
def caminhao_oficina_salvar():
    if not session.get('logado'): return jsonify({'status': 'erro', 'msg': 'Não autenticado'}), 401
    if not (is_admin_session() or tem_acesso_modulo('ferramentaria')):
        return jsonify({'status': 'erro', 'msg': 'Sem permissão'}), 403
    caminhao_id = request.form.get('id', '').strip()
    identificacao = request.form.get('identificacao', '').strip().upper()
    responsavel = request.form.get('responsavel', '').strip().upper()
    observacao = request.form.get('observacao', '').strip()
    codigos = request.form.getlist('codigo[]')
    descricoes = request.form.getlist('descricao[]')
    quantidades = request.form.getlist('quantidade[]')
    valores = request.form.getlist('valor_unitario[]')
    observacoes_item = request.form.getlist('observacao_item[]')
    if not identificacao:
        return jsonify({'status': 'erro', 'msg': 'Identificação do caminhão é obrigatória.'})
    itens = []
    for idx, (cod, desc, qty, val) in enumerate(zip(codigos, descricoes, quantidades, valores)):
        desc = desc.strip()
        if not desc:
            continue
        try: qty_f = float(str(qty).replace(',', '.'))
        except: qty_f = 1.0
        try: val_f = float(str(val).replace(',', '.'))
        except: val_f = 0.0
        obs_item = observacoes_item[idx].strip() if idx < len(observacoes_item) else ''
        itens.append((cod.strip(), desc, qty_f, val_f, obs_item))
    conn = get_db_connection()
    try:
        agora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        usuario = session.get('usuario', '')
        if caminhao_id:
            conn.execute('UPDATE caminhao_oficina SET identificacao=?, responsavel=?, observacao=?, atualizado_em=? WHERE id=?',
                         (identificacao, responsavel, observacao, agora, int(caminhao_id)))
            conn.execute('DELETE FROM caminhao_oficina_itens WHERE caminhao_id=?', (int(caminhao_id),))
            novo_id = int(caminhao_id)
        else:
            cur = conn.execute('INSERT INTO caminhao_oficina (identificacao, responsavel, observacao, criado_por, criado_em, atualizado_em) VALUES (?,?,?,?,?,?)',
                               (identificacao, responsavel, observacao, usuario, agora, agora))
            novo_id = cur.lastrowid
        if itens:
            conn.executemany('INSERT INTO caminhao_oficina_itens (caminhao_id, codigo, descricao, quantidade, valor_unitario, observacao) VALUES (?,?,?,?,?,?)',
                             [(novo_id, c, d, q, v, o) for c, d, q, v, o in itens])
        conn.commit()
        conn.close()
        return jsonify({'status': 'ok', 'id': novo_id})
    except Exception as e:
        conn.close()
        return jsonify({'status': 'erro', 'msg': str(e)})


@app.route('/api/ferramentaria/caminhoes/<int:caminhao_id>/itens')
def caminhao_oficina_itens(caminhao_id):
    if not session.get('logado'): return jsonify({'status': 'erro'}), 401
    conn = get_db_connection()
    caminhao = conn.execute('SELECT * FROM caminhao_oficina WHERE id=?', (caminhao_id,)).fetchone()
    itens = conn.execute('SELECT * FROM caminhao_oficina_itens WHERE caminhao_id=? ORDER BY id', (caminhao_id,)).fetchall()
    conn.close()
    if not caminhao:
        return jsonify({'status': 'erro', 'msg': 'Não encontrado'}), 404
    return jsonify({
        'status': 'ok',
        'caminhao': dict(caminhao),
        'itens': [dict(i) for i in itens]
    })


@app.route('/api/ferramentaria/caminhoes/excluir/<int:caminhao_id>', methods=['POST'])
def caminhao_oficina_excluir(caminhao_id):
    if not session.get('logado'): return jsonify({'status': 'erro', 'msg': 'Não autenticado'}), 401
    if not is_admin_session():
        return jsonify({'status': 'erro', 'msg': 'Apenas administradores podem excluir.'}), 403
    conn = get_db_connection()
    try:
        conn.execute('DELETE FROM caminhao_oficina_itens WHERE caminhao_id=?', (caminhao_id,))
        conn.execute('DELETE FROM caminhao_oficina WHERE id=?', (caminhao_id,))
        conn.commit()
        conn.close()
        return jsonify({'status': 'ok'})
    except Exception as e:
        conn.close()
        return jsonify({'status': 'erro', 'msg': str(e)})


# ── Termos de Responsabilidade (PDF) ─────────────────────────────────────────

UPLOAD_TERMOS_DIR = os.path.join(BASE_DIR, 'uploads', 'termos')
ALLOWED_EXTENSIONS = {'pdf'}

def _extensao_permitida(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@app.route('/ferramentaria/termos/<path:filename>')
def servir_termo(filename):
    if not session.get('logado'): return redirect(url_for('login'))
    if not (is_admin_session() or tem_acesso_modulo('ferramentaria')):
        return 'Sem permissão', 403
    filepath = os.path.join(UPLOAD_TERMOS_DIR, secure_filename(filename))
    if not os.path.exists(filepath):
        return 'Arquivo não encontrado', 404
    return send_file(filepath, mimetype='application/pdf')


@app.route('/api/ferramentaria/caixas/<int:caixa_id>/upload_termo', methods=['POST'])
def caixa_upload_termo(caixa_id):
    if not session.get('logado'): return jsonify({'status': 'erro', 'msg': 'Não autenticado'}), 401
    if not (is_admin_session() or tem_acesso_modulo('ferramentaria')):
        return jsonify({'status': 'erro', 'msg': 'Sem permissão'}), 403
    if 'file' not in request.files:
        return jsonify({'status': 'erro', 'msg': 'Nenhum arquivo enviado.'})
    f = request.files['file']
    if not f or f.filename == '':
        return jsonify({'status': 'erro', 'msg': 'Arquivo inválido.'})
    if not _extensao_permitida(f.filename):
        return jsonify({'status': 'erro', 'msg': 'Apenas arquivos PDF são aceitos.'})
    conn = get_db_connection()
    try:
        row = conn.execute('SELECT termo_pdf FROM caixa_ferramentas WHERE id=?', (caixa_id,)).fetchone()
        if not row:
            conn.close()
            return jsonify({'status': 'erro', 'msg': 'Caixa não encontrada.'})
        if row['termo_pdf']:
            antigo = os.path.join(UPLOAD_TERMOS_DIR, row['termo_pdf'])
            if os.path.exists(antigo):
                os.remove(antigo)
        ts = datetime.now().strftime('%Y%m%d%H%M%S')
        nome_arquivo = f'caixa_{caixa_id}_{ts}.pdf'
        os.makedirs(UPLOAD_TERMOS_DIR, exist_ok=True)
        f.save(os.path.join(UPLOAD_TERMOS_DIR, nome_arquivo))
        conn.execute('UPDATE caixa_ferramentas SET termo_pdf=?, atualizado_em=? WHERE id=?',
                     (nome_arquivo, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), caixa_id))
        conn.commit()
        conn.close()
        return jsonify({'status': 'ok', 'filename': nome_arquivo,
                        'url': url_for('servir_termo', filename=nome_arquivo)})
    except Exception as e:
        conn.close()
        return jsonify({'status': 'erro', 'msg': str(e)})


@app.route('/api/ferramentaria/caixas/<int:caixa_id>/excluir_termo', methods=['POST'])
def caixa_excluir_termo(caixa_id):
    if not session.get('logado'): return jsonify({'status': 'erro', 'msg': 'Não autenticado'}), 401
    if not (is_admin_session() or tem_acesso_modulo('ferramentaria')):
        return jsonify({'status': 'erro', 'msg': 'Sem permissão'}), 403
    conn = get_db_connection()
    try:
        row = conn.execute('SELECT termo_pdf FROM caixa_ferramentas WHERE id=?', (caixa_id,)).fetchone()
        if row and row['termo_pdf']:
            filepath = os.path.join(UPLOAD_TERMOS_DIR, row['termo_pdf'])
            if os.path.exists(filepath):
                os.remove(filepath)
        conn.execute("UPDATE caixa_ferramentas SET termo_pdf='' WHERE id=?", (caixa_id,))
        conn.commit()
        conn.close()
        return jsonify({'status': 'ok'})
    except Exception as e:
        conn.close()
        return jsonify({'status': 'erro', 'msg': str(e)})


@app.route('/api/ferramentaria/caminhoes/<int:caminhao_id>/upload_termo', methods=['POST'])
def caminhao_upload_termo(caminhao_id):
    if not session.get('logado'): return jsonify({'status': 'erro', 'msg': 'Não autenticado'}), 401
    if not (is_admin_session() or tem_acesso_modulo('ferramentaria')):
        return jsonify({'status': 'erro', 'msg': 'Sem permissão'}), 403
    if 'file' not in request.files:
        return jsonify({'status': 'erro', 'msg': 'Nenhum arquivo enviado.'})
    f = request.files['file']
    if not f or f.filename == '':
        return jsonify({'status': 'erro', 'msg': 'Arquivo inválido.'})
    if not _extensao_permitida(f.filename):
        return jsonify({'status': 'erro', 'msg': 'Apenas arquivos PDF são aceitos.'})
    conn = get_db_connection()
    try:
        row = conn.execute('SELECT termo_pdf FROM caminhao_oficina WHERE id=?', (caminhao_id,)).fetchone()
        if not row:
            conn.close()
            return jsonify({'status': 'erro', 'msg': 'Caminhão não encontrado.'})
        if row['termo_pdf']:
            antigo = os.path.join(UPLOAD_TERMOS_DIR, row['termo_pdf'])
            if os.path.exists(antigo):
                os.remove(antigo)
        ts = datetime.now().strftime('%Y%m%d%H%M%S')
        nome_arquivo = f'caminhao_{caminhao_id}_{ts}.pdf'
        os.makedirs(UPLOAD_TERMOS_DIR, exist_ok=True)
        f.save(os.path.join(UPLOAD_TERMOS_DIR, nome_arquivo))
        conn.execute('UPDATE caminhao_oficina SET termo_pdf=?, atualizado_em=? WHERE id=?',
                     (nome_arquivo, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), caminhao_id))
        conn.commit()
        conn.close()
        return jsonify({'status': 'ok', 'filename': nome_arquivo,
                        'url': url_for('servir_termo', filename=nome_arquivo)})
    except Exception as e:
        conn.close()
        return jsonify({'status': 'erro', 'msg': str(e)})


@app.route('/api/ferramentaria/caminhoes/<int:caminhao_id>/excluir_termo', methods=['POST'])
def caminhao_excluir_termo(caminhao_id):
    if not session.get('logado'): return jsonify({'status': 'erro', 'msg': 'Não autenticado'}), 401
    if not (is_admin_session() or tem_acesso_modulo('ferramentaria')):
        return jsonify({'status': 'erro', 'msg': 'Sem permissão'}), 403
    conn = get_db_connection()
    try:
        row = conn.execute('SELECT termo_pdf FROM caminhao_oficina WHERE id=?', (caminhao_id,)).fetchone()
        if row and row['termo_pdf']:
            filepath = os.path.join(UPLOAD_TERMOS_DIR, row['termo_pdf'])
            if os.path.exists(filepath):
                os.remove(filepath)
        conn.execute("UPDATE caminhao_oficina SET termo_pdf='' WHERE id=?", (caminhao_id,))
        conn.commit()
        conn.close()
        return jsonify({'status': 'ok'})
    except Exception as e:
        conn.close()
        return jsonify({'status': 'erro', 'msg': str(e)})


# ── Conferência de estoque do Caminhão Oficina ────────────────────────────────
@app.route('/api/ferramentaria/conferencia/salvar', methods=['POST'])
def conferencia_caminhao_salvar():
    if not session.get('logado'): return jsonify({'status': 'erro', 'msg': 'Não autenticado'}), 401
    if not (is_admin_session() or tem_acesso_modulo('ferramentaria')):
        return jsonify({'status': 'erro', 'msg': 'Sem permissão'}), 403

    dados = request.get_json(silent=True) or {}
    try:
        caminhao_id = int(dados.get('caminhao_id'))
    except (TypeError, ValueError):
        return jsonify({'status': 'erro', 'msg': 'Caminhão inválido.'})
    observacao = str(dados.get('observacao', '')).strip()
    itens = dados.get('itens') or []
    if not itens:
        return jsonify({'status': 'erro', 'msg': 'Nenhum item para conferir.'})

    conn = get_db_connection()
    try:
        caminhao = conn.execute('SELECT id FROM caminhao_oficina WHERE id=?', (caminhao_id,)).fetchone()
        if not caminhao:
            conn.close()
            return jsonify({'status': 'erro', 'msg': 'Caminhão não encontrado.'})

        agora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        usuario = session.get('perfil_nome', '')
        total_itens = len(itens)
        total_faltando = sum(1 for i in itens if not i.get('presente', True))

        cur = conn.execute('''
            INSERT INTO caminhao_conferencias
                (caminhao_id, data_conferencia, usuario, observacao, total_itens, total_faltando, criado_em)
            VALUES (?,?,?,?,?,?,?)
        ''', (caminhao_id, agora, usuario, observacao, total_itens, total_faltando, agora))
        conferencia_id = cur.lastrowid

        linhas = []
        for i in itens:
            desc = str(i.get('descricao', '')).strip()
            if not desc:
                continue
            try: qtd = float(str(i.get('quantidade_esperada', 0)).replace(',', '.'))
            except (TypeError, ValueError): qtd = 0.0
            linhas.append((
                conferencia_id,
                str(i.get('codigo', '')).strip(),
                desc,
                qtd,
                1 if i.get('presente', True) else 0,
                str(i.get('observacao', '')).strip(),
            ))
        if linhas:
            conn.executemany('''
                INSERT INTO caminhao_conferencia_itens
                    (conferencia_id, codigo, descricao, quantidade_esperada, presente, observacao)
                VALUES (?,?,?,?,?,?)
            ''', linhas)
        conn.commit()
        conn.close()
        return jsonify({'status': 'ok', 'id': conferencia_id, 'total_faltando': total_faltando})
    except Exception as e:
        conn.close()
        return jsonify({'status': 'erro', 'msg': str(e)})


@app.route('/api/ferramentaria/conferencia/<int:conferencia_id>')
def conferencia_caminhao_detalhe(conferencia_id):
    if not session.get('logado'): return jsonify({'status': 'erro'}), 401
    conn = get_db_connection()
    conferencia = conn.execute('''
        SELECT co.*, c.identificacao, c.responsavel
        FROM caminhao_conferencias co
        JOIN caminhao_oficina c ON c.id = co.caminhao_id
        WHERE co.id = ?
    ''', (conferencia_id,)).fetchone()
    if not conferencia:
        conn.close()
        return jsonify({'status': 'erro', 'msg': 'Conferência não encontrada.'}), 404
    itens = conn.execute('''
        SELECT * FROM caminhao_conferencia_itens WHERE conferencia_id = ? ORDER BY presente ASC, descricao
    ''', (conferencia_id,)).fetchall()
    conn.close()
    return jsonify({
        'status': 'ok',
        'conferencia': dict(conferencia),
        'itens': [dict(i) for i in itens]
    })


@app.route('/api/ferramentaria/conferencia/excluir/<int:conferencia_id>', methods=['POST'])
def conferencia_caminhao_excluir(conferencia_id):
    if not session.get('logado'): return jsonify({'status': 'erro', 'msg': 'Não autenticado'}), 401
    if not is_admin_session():
        return jsonify({'status': 'erro', 'msg': 'Apenas administradores podem excluir.'}), 403
    conn = get_db_connection()
    try:
        conn.execute('DELETE FROM caminhao_conferencia_itens WHERE conferencia_id=?', (conferencia_id,))
        conn.execute('DELETE FROM caminhao_conferencias WHERE id=?', (conferencia_id,))
        conn.commit()
        conn.close()
        return jsonify({'status': 'ok'})
    except Exception as e:
        conn.close()
        return jsonify({'status': 'erro', 'msg': str(e)})


# ── Dashboard de Frentes ──────────────────────────────────────────────────────
@app.route('/dash_frentes')
def dash_frentes():
    if not session.get('logado'):
        return redirect(url_for('login'))
    if not (is_admin_session() or tem_acesso_modulo('externos')):
        flash('Sem permissão para acessar o Dashboard de Frentes.', 'danger')
        return redirect(url_for('admin'))

    conn = get_db_connection()
    frotas_rows = conn.execute('SELECT * FROM frotas_disp').fetchall()

    # frotas_disp é alimentada pelo sync com o ERP. Quando ela está vazia (sync
    # indisponível), usamos o cadastro local de frotas para a tela não ficar
    # inteiramente em branco - a origem é sinalizada no template.
    origem_frotas = 'sync'
    if not frotas_rows:
        origem_frotas = 'cadastro'
        frotas_rows = conn.execute('''
            SELECT id_frota as cod_frota, id_frota,
                   COALESCE(NULLIF(TRIM(descricao), ''), 'SEM DESCRIÇÃO') as descricao,
                   COALESCE(NULLIF(TRIM(especialidade), ''), 'OUTROS')    as especialidade,
                   COALESCE(NULLIF(TRIM(agrupamento), ''), 'OUTROS')      as agrupamento,
                   'SIM' as proprio
            FROM frotas
        ''').fetchall()

    os_rows     = conn.execute(
        "SELECT veiculo, status, manutencao, data_abertura, dias_aberta, "
        "descricao_problema, data_liberacao, descricao_veiculo, especialidade "
        "FROM os_registros WHERE status IN ('A','E')"
    ).fetchall()
    cfg_rows    = conn.execute('SELECT cod_frota, frente FROM frentes_config').fetchall()
    sync_row    = conn.execute("SELECT ultima_sync FROM sync_log WHERE chave='os'").fetchone()
    conn.close()

    cfg = {r['cod_frota']: r['frente'] for r in cfg_rows}
    frotas = [dict(r) for r in frotas_rows]
    for f in frotas:
        f['frente'] = cfg.get(f['cod_frota'], '')

    return render_template('dash_frentes.html',
        frotas_data  = frotas,
        os_data      = [dict(r) for r in os_rows],
        origem_frotas = origem_frotas,
        total_config_frente = len(cfg),
        ultima_sync  = sync_row['ultima_sync'] if sync_row else '—',
        data_geracao = datetime.now().strftime('%d/%m/%Y %H:%M:%S'),
    )


@app.route('/api/sync_agora', methods=['POST'])
def api_sync_agora():
    if not session.get('logado'):
        return jsonify({'ok': False}), 403
    try:
        from db_empresa import sync_os, sync_frotas
        what = (request.get_json() or {}).get('what', 'os')
        if what == 'frotas':
            sync_frotas()
        else:
            sync_os()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)})


# Token simples para o script local se autenticar sem precisar de sessão
_SYNC_TOKEN = 'pcm_sync_japungu_2025'

@app.route('/api/push_sync', methods=['POST'])
def api_push_sync():
    """
    Endpoint chamado pelo script local (sync_local.py).
    Recebe JSON com frotas[] e/ou os[] e salva no SQLite.
    Autenticação via header X-Sync-Token.
    """
    if request.headers.get('X-Sync-Token') != _SYNC_TOKEN:
        return jsonify({'ok': False, 'msg': 'Token inválido'}), 403

    payload = request.get_json(force=True) or {}
    conn = get_db_connection()

    salvos = {}

    if 'frotas' in payload:
        rows = payload['frotas']
        conn.execute('DELETE FROM frotas_disp')
        conn.executemany(
            'INSERT INTO frotas_disp (id_frota, descricao, especialidade, agrupamento, proprio, cod_frota) VALUES (?,?,?,?,?,?)',
            [(r['cod_frota'], r['descricao'], r['especialidade'], r['agrupamento'], 'SIM', r['cod_frota']) for r in rows]
        )
        conn.execute(
            "INSERT OR REPLACE INTO sync_log (chave, ultima_sync, total) VALUES ('frotas', ?, ?)",
            (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), len(rows))
        )
        salvos['frotas'] = len(rows)

    if 'os' in payload:
        rows = payload['os']
        conn.execute('DELETE FROM os_registros')
        conn.executemany('''
            INSERT INTO os_registros (
                nro_os, status, data_abertura, veiculo, placa, descricao_veiculo,
                manutencao, centro_custo_nome, oficina_nome, tipo_os,
                solicitante_nome, data_liberacao, fundo_agricola,
                custo_oficina, custo_mao_obra, pecas_consumidas, valor_os,
                descricao_problema, especialidade, dias_aberta
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ''', [
            (r['nro_os'], r['status'], r['data_abertura'], r['veiculo'], '',
             r['descricao_veiculo'], r['manutencao'], r['centro_custo_nome'],
             r['oficina_nome'], r['tipo_os'], r['solicitante_nome'],
             r['data_liberacao'], '', 0.0, 0.0, 0.0, 0.0,
             r['descricao_problema'], r['agrupamento'], r['dias_aberta'])
            for r in rows
        ])
        conn.execute(
            "INSERT OR REPLACE INTO sync_log (chave, ultima_sync, total) VALUES ('os', ?, ?)",
            (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), len(rows))
        )
        salvos['os'] = len(rows)

    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'salvos': salvos})


@app.route('/api/mover_frota', methods=['POST'])
def mover_frota():
    """Move uma frota entre setor/especialidade/frente - o endpoint por trás
    do arrastar-e-soltar da tela de Estrutura Operacional. Um campo por
    chamada, porque o arraste é sempre um movimento de cada vez.
    """
    if not session.get('logado'):
        return jsonify({'status': 'erro', 'msg': 'Não autenticado'}), 401
    if not (is_admin_session() or tem_acesso_modulo('programacao')):
        return jsonify({'status': 'erro', 'msg': 'Sem permissão'}), 403

    dados = request.get_json(silent=True) or {}
    id_frota = str(dados.get('id_frota') or '').strip()
    campo = (dados.get('campo') or '').strip().lower()
    valor = str(dados.get('valor') or '').strip().upper()
    if not id_frota or campo not in ('setor', 'especialidade', 'frente'):
        return jsonify({'status': 'erro', 'msg': 'Dados inválidos.'}), 400

    conn = get_db_connection()
    if not conn.execute('SELECT 1 FROM frotas WHERE id_frota = ?', (id_frota,)).fetchone():
        conn.close()
        return jsonify({'status': 'erro', 'msg': 'Frota não encontrada.'}), 404

    if campo == 'frente':
        if valor:
            conn.execute('INSERT OR REPLACE INTO frentes_config (cod_frota, frente) VALUES (?,?)', (id_frota, valor))
        else:
            conn.execute('DELETE FROM frentes_config WHERE cod_frota = ?', (id_frota,))
    else:
        conn.execute(f'UPDATE frotas SET {campo} = ? WHERE id_frota = ?', (valor or 'OUTROS', id_frota))

    conn.commit()
    conn.close()
    return jsonify({'status': 'sucesso', 'id_frota': id_frota, 'campo': campo, 'valor': valor})


@app.route('/relatorio_pendencias')
def relatorio_pendencias():
    """Todas as frotas pendentes do sistema, num lugar só, organizadas por
    Especialidade -> Setor -> Frente -> Frota -> Pendência (Foto 20 do pedido).
    Cada tela de programação já mostra isto espalhado por tipo de serviço;
    esta reúne tudo, com filtro combinável por especialidade/setor/frente.
    """
    if not session.get('logado'):
        return redirect(url_for('login'))
    if not tem_acesso_modulo('programacao'):
        flash('Sem permissão para acessar o relatório de pendências.', 'danger')
        return redirect(url_for('admin'))

    f_especialidade = (request.args.get('especialidade') or '').strip()
    f_setor = (request.args.get('setor') or '').strip()
    f_frente = (request.args.get('frente') or '').strip()
    hoje = datetime.now().strftime('%Y-%m-%d')

    conn = get_db_connection()
    mapa_setor, sem_setor = _mapa_agrupamento(conn, 'setor')
    mapa_frente, sem_frente_valor = _mapa_agrupamento(conn, 'frente')
    linhas = conn.execute('''
        SELECT p.id, p.id_frota, p.tipo_servico, p.data_planejada,
               COALESCE(NULLIF(TRIM(p.status), ''), 'PENDENTE') as status,
               COALESCE(NULLIF(TRIM(f.descricao), ''), 'SEM DESCRIÇÃO') as descricao,
               COALESCE(NULLIF(TRIM(f.especialidade), ''), 'OUTROS') as especialidade
        FROM programacao_semanal p
        LEFT JOIN frotas f ON f.id_frota = p.id_frota
        WHERE COALESCE(NULLIF(TRIM(p.status), ''), 'PENDENTE') IN ('PENDENTE', 'BLOQUEADA')
        ORDER BY p.data_planejada
    ''').fetchall()

    # Motivo de quem está BLOQUEADA na oficina agora, pra não mostrar só "BLOQUEADA" seco.
    motivos_bloqueio = {r['id_frota']: r['motivo'] for r in conn.execute(
        "SELECT id_frota, motivo FROM historico_bloqueios WHERE data_desbloqueio IS NULL")}
    conn.close()

    # Especialidade -> Setor -> Frente -> Frota -> [pendencias]
    arvore = {}
    total_geral = 0
    especialidades_vistas, setores_vistos, frentes_vistas = set(), set(), set()

    for r in linhas:
        setor = mapa_setor.get(r['id_frota'], sem_setor)
        frente = mapa_frente.get(r['id_frota'], '') or 'SEM FRENTE'
        esp = r['especialidade']
        especialidades_vistas.add(esp); setores_vistos.add(setor); frentes_vistas.add(frente)

        if f_especialidade and esp != f_especialidade: continue
        if f_setor and setor != f_setor: continue
        if f_frente and frente != f_frente: continue

        atrasada = r['data_planejada'] < hoje
        motivo = motivos_bloqueio.get(r['id_frota']) if r['status'] == 'BLOQUEADA' else None
        pend = {
            'tipo_servico': r['tipo_servico'], 'data': r['data_planejada'],
            'status': r['status'], 'atrasada': atrasada, 'motivo': motivo,
        }
        n_esp = arvore.setdefault(esp, {})
        n_setor = n_esp.setdefault(setor, {})
        n_frente = n_setor.setdefault(frente, {})
        n_frota = n_frente.setdefault(r['id_frota'], {'descricao': r['descricao'], 'pendencias': []})
        n_frota['pendencias'].append(pend)
        total_geral += 1

    # Acha pra HTML: uma lista aninhada, ordenada, com contagens em cada nivel -
    # os totais somam os filhos, então o cabeçalho de cada grupo já mostra "quantas".
    def montar_especialidades():
        out = []
        for esp in sorted(arvore):
            setores_out = []
            total_esp = 0
            for setor in sorted(arvore[esp]):
                frentes_out = []
                total_setor = 0
                for frente in sorted(arvore[esp][setor]):
                    frotas_out = []
                    total_frente = 0
                    for id_frota in sorted(arvore[esp][setor][frente]):
                        d = arvore[esp][setor][frente][id_frota]
                        frotas_out.append({'id_frota': id_frota, 'descricao': d['descricao'],
                                           'pendencias': sorted(d['pendencias'], key=lambda p: p['data'])})
                        total_frente += len(d['pendencias'])
                    frentes_out.append({'nome': frente, 'frotas': frotas_out, 'total': total_frente})
                    total_setor += total_frente
                setores_out.append({'nome': setor, 'frentes': frentes_out, 'total': total_setor})
                total_esp += total_setor
            out.append({'nome': esp, 'setores': setores_out, 'total': total_esp})
        return out

    return render_template('relatorio_pendencias.html',
        especialidades=montar_especialidades(), total_geral=total_geral,
        opcoes_especialidade=sorted(especialidades_vistas),
        opcoes_setor=sorted(setores_vistos), opcoes_frente=sorted(frentes_vistas),
        f_especialidade=f_especialidade, f_setor=f_setor, f_frente=f_frente,
        data_geracao=datetime.now().strftime('%d/%m/%Y %H:%M'))


@app.route('/gestao_estrutura')
def gestao_estrutura():
    """Setor -> Especialidade -> Frente -> Frotas, numa tela só, com as
    frotas arrastáveis entre colunas (Foto 3/4 do pedido). Reaproveita as
    mesmas colunas (frotas.setor/especialidade + frentes_config) que o resto
    do sistema já usa - não é um cadastro paralelo."""
    if not session.get('logado'):
        return redirect(url_for('login'))
    if not is_admin_session():
        flash('Apenas administradores podem reorganizar a estrutura de frotas.', 'danger')
        return redirect(url_for('admin'))

    conn = get_db_connection()
    frotas_rows = conn.execute('''
        SELECT id_frota, COALESCE(NULLIF(TRIM(descricao),''),'SEM DESCRIÇÃO') as descricao,
               COALESCE(NULLIF(TRIM(setor),''),'SEM SETOR') as setor,
               COALESCE(NULLIF(TRIM(especialidade),''),'OUTROS') as especialidade
        FROM frotas ORDER BY id_frota
    ''').fetchall()
    frentes_rows = conn.execute('SELECT cod_frota, frente FROM frentes_config').fetchall()
    conn.close()

    mapa_frente = {r['cod_frota']: r['frente'] for r in frentes_rows}
    frotas = [dict(r) for r in frotas_rows]
    for f in frotas:
        f['frente'] = mapa_frente.get(f['id_frota'], '')

    setores = sorted({f['setor'] for f in frotas})
    especialidades = sorted({f['especialidade'] for f in frotas})
    frentes_existentes = sorted({v for v in mapa_frente.values() if v})

    # ?embutido=1: sem sidebar/topbar próprios - é como a aba "Arrastar" do
    # botão Estrutura, na Programação Semanal, mostra isto dentro de um iframe.
    embutido = request.args.get('embutido') == '1'

    return render_template('gestao_estrutura.html',
        frotas=frotas, setores=setores, especialidades=especialidades,
        frentes_existentes=frentes_existentes, embutido=embutido)


@app.route('/api/salvar_frentes_config', methods=['POST'])
def salvar_frentes_config():
    if not session.get('logado'):
        return jsonify({'ok': False, 'msg': 'Não autorizado'}), 403
    data = request.get_json() or []
    conn = get_db_connection()
    conn.execute('DELETE FROM frentes_config')
    for item in data:
        cod    = str(item.get('cod_frota', '')).strip()
        frente = str(item.get('frente', '')).strip()
        if cod and frente:
            conn.execute(
                'INSERT OR REPLACE INTO frentes_config (cod_frota, frente) VALUES (?,?)',
                (cod, frente)
            )
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


# ══════════════════════════════════════════════════════════════════════════════
# MÓDULO: COMPRAS / SOLICITAÇÕES
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/compras')
def compras():
    if not session.get('logado'):
        return redirect(url_for('login'))
    if not (is_admin_session() or tem_acesso_modulo('compras')):
        flash('Sem permissão para acessar Compras & Pedidos.', 'danger')
        return redirecionar_primeiro_modulo_permitido()

    filtro_status = request.args.get('status', 'ABERTO')
    conn = get_db_connection()

    if filtro_status == 'TODOS':
        solicitacoes = conn.execute('''
            SELECT * FROM compras_solicitacoes
            ORDER BY
                CASE prioridade WHEN 'CRÍTICO' THEN 1 WHEN 'URGENTE' THEN 2 ELSE 3 END,
                criado_em DESC
        ''').fetchall()
    else:
        solicitacoes = conn.execute('''
            SELECT * FROM compras_solicitacoes
            WHERE status = ?
            ORDER BY
                CASE prioridade WHEN 'CRÍTICO' THEN 1 WHEN 'URGENTE' THEN 2 ELSE 3 END,
                criado_em DESC
        ''', (filtro_status,)).fetchall()

    stats = conn.execute('''
        SELECT
            COALESCE(SUM(CASE WHEN status = 'ABERTO'      THEN 1 ELSE 0 END), 0) as abertos,
            COALESCE(SUM(CASE WHEN status = 'EM COTAÇÃO'  THEN 1 ELSE 0 END), 0) as em_cotacao,
            COALESCE(SUM(CASE WHEN status = 'APROVADO'    THEN 1 ELSE 0 END), 0) as aprovados,
            COALESCE(SUM(CASE WHEN status = 'COMPRADO'    THEN 1 ELSE 0 END), 0) as comprados,
            COALESCE(SUM(CASE WHEN status = 'CANCELADO'   THEN 1 ELSE 0 END), 0) as cancelados,
            COUNT(*) as total
        FROM compras_solicitacoes
    ''').fetchone()

    frotas = conn.execute('SELECT id_frota, descricao FROM frotas ORDER BY id_frota').fetchall()

    # Carrega todos os itens de uma vez e agrupa por solicitacao_id
    itens_rows = conn.execute('SELECT * FROM compras_itens ORDER BY id').fetchall()
    itens_por_sol = {}
    for it in itens_rows:
        itens_por_sol.setdefault(it['solicitacao_id'], []).append(dict(it))

    conn.close()

    solicitacoes_lista = []
    for s in solicitacoes:
        sd = dict(s)
        sd['itens'] = itens_por_sol.get(s['id'], [])
        solicitacoes_lista.append(sd)

    return render_template('compras.html',
        solicitacoes=solicitacoes_lista,
        stats=dict(stats) if stats else {},
        filtro_status=filtro_status,
        frotas=frotas,
        hoje=datetime.now().strftime('%Y-%m-%d')
    )


@app.route('/api/compras/nova', methods=['POST'])
def compras_nova():
    if not session.get('logado'):
        return redirect(url_for('login'))

    data_sol      = request.form.get('data_solicitacao', '').strip()
    fornecedor    = request.form.get('fornecedor', '').strip().upper()
    frota         = request.form.get('frota', '').strip().upper()
    nro_orcamento = request.form.get('nro_orcamento', '').strip()
    nro_sol       = request.form.get('nro_solicitacao', '').strip()
    descricao     = request.form.get('descricao', '').strip().upper()
    prioridade    = request.form.get('prioridade', 'NORMAL').strip().upper()
    observacao    = request.form.get('observacao', '').strip()
    encarregado   = request.form.get('encarregado', '').strip().upper()

    if not data_sol:
        flash('Informe a data da solicitação.', 'danger')
        return redirect(url_for('compras'))

    conn = get_db_connection()

    # Verificação de duplicidade por nº solicitação
    if nro_sol:
        dup = conn.execute(
            "SELECT id FROM compras_solicitacoes WHERE nro_solicitacao = ? AND status NOT IN ('COMPRADO','CANCELADO')",
            (nro_sol,)
        ).fetchone()
        if dup:
            conn.close()
            flash(f'Duplicidade detectada: Nº de solicitação {nro_sol} já está em aberto (ID #{dup["id"]}). Verifique antes de cadastrar novamente.', 'warning')
            return redirect(url_for('compras'))

    # Verificação de duplicidade por nº orçamento
    if nro_orcamento:
        dup = conn.execute(
            "SELECT id FROM compras_solicitacoes WHERE nro_orcamento = ? AND status NOT IN ('COMPRADO','CANCELADO')",
            (nro_orcamento,)
        ).fetchone()
        if dup:
            conn.close()
            flash(f'Duplicidade detectada: Nº de orçamento {nro_orcamento} já está em aberto (ID #{dup["id"]}). Verifique antes de cadastrar novamente.', 'warning')
            return redirect(url_for('compras'))

    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO compras_solicitacoes
        (data_solicitacao, fornecedor, frota, nro_orcamento, nro_solicitacao,
         descricao, status, prioridade, criado_por, criado_em, observacao, encarregado)
        VALUES (?, ?, ?, ?, ?, ?, 'ABERTO', ?, ?, ?, ?, ?)
    ''', (
        data_sol, fornecedor, frota, nro_orcamento, nro_sol,
        descricao, prioridade,
        session.get('perfil_nome', ''),
        datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        observacao, encarregado
    ))
    sol_id = cursor.lastrowid

    # Salva itens (arrays do formulário)
    item_cods  = request.form.getlist('item_codigo[]')
    item_descs = request.form.getlist('item_descricao[]')
    item_qtds  = request.form.getlist('item_quantidade[]')
    item_units = request.form.getlist('item_unidade[]')
    for i, desc in enumerate(item_descs):
        desc = desc.strip().upper()
        cod  = (item_cods[i]  if i < len(item_cods)  else '').strip()
        if not desc and not cod:
            continue
        qtd  = (item_qtds[i]  if i < len(item_qtds)  else '').strip()
        unit = (item_units[i] if i < len(item_units) else '').strip().upper()
        cursor.execute(
            'INSERT INTO compras_itens (solicitacao_id, codigo, descricao, quantidade, unidade) VALUES (?,?,?,?,?)',
            (sol_id, cod, desc or cod, qtd, unit)
        )

    conn.commit()
    conn.close()
    flash('Solicitação de compra registrada com sucesso!', 'success')
    return redirect(url_for('compras'))


@app.route('/api/compras/atualizar/<int:id_sol>', methods=['POST'])
def compras_atualizar(id_sol):
    if not session.get('logado'):
        return jsonify({'status': 'erro'}), 403

    dados = request.get_json(force=True) or {}
    novo_status    = dados.get('status', '').strip()
    novo_fornecedor    = dados.get('fornecedor', '').strip().upper()
    novo_nro_orc       = dados.get('nro_orcamento', '').strip()
    novo_nro_sol       = dados.get('nro_solicitacao', '').strip()
    nova_obs           = dados.get('observacao', '').strip()
    nova_prior         = dados.get('prioridade', '').strip().upper()
    novo_encarregado   = dados.get('encarregado', None)

    conn = get_db_connection()
    if novo_status:
        data_conclusao = datetime.now().strftime('%Y-%m-%d') if novo_status in ('COMPRADO', 'CANCELADO') else None
        conn.execute(
            'UPDATE compras_solicitacoes SET status = ?, data_conclusao = ? WHERE id = ?',
            (novo_status, data_conclusao, id_sol)
        )
    if novo_fornecedor:
        conn.execute('UPDATE compras_solicitacoes SET fornecedor = ? WHERE id = ?', (novo_fornecedor, id_sol))
    if novo_nro_orc:
        conn.execute('UPDATE compras_solicitacoes SET nro_orcamento = ? WHERE id = ?', (novo_nro_orc, id_sol))
    if novo_nro_sol:
        conn.execute('UPDATE compras_solicitacoes SET nro_solicitacao = ? WHERE id = ?', (novo_nro_sol, id_sol))
    if nova_obs is not None:
        conn.execute('UPDATE compras_solicitacoes SET observacao = ? WHERE id = ?', (nova_obs, id_sol))
    if nova_prior:
        conn.execute('UPDATE compras_solicitacoes SET prioridade = ? WHERE id = ?', (nova_prior, id_sol))
    if novo_encarregado is not None:
        conn.execute('UPDATE compras_solicitacoes SET encarregado = ? WHERE id = ?', (novo_encarregado.strip().upper(), id_sol))
    conn.commit()
    conn.close()
    return jsonify({'status': 'ok'})


@app.route('/api/compras/excluir/<int:id_sol>', methods=['POST'])
def compras_excluir(id_sol):
    if not session.get('logado'):
        return redirect(url_for('login'))
    if not is_admin_session():
        flash('Apenas o administrador pode excluir solicitações.', 'danger')
        return redirect(url_for('compras'))
    conn = get_db_connection()
    conn.execute('DELETE FROM compras_solicitacoes WHERE id = ?', (id_sol,))
    conn.commit()
    conn.close()
    flash('Solicitação excluída com sucesso.', 'danger')
    return redirect(url_for('compras'))


@app.route('/api/compras/verificar')
def compras_verificar():
    if not session.get('logado'):
        return jsonify({'duplicado': False})
    nro_sol = request.args.get('nro_solicitacao', '').strip()
    nro_orc = request.args.get('nro_orcamento', '').strip()
    if not nro_sol and not nro_orc:
        return jsonify({'duplicado': False})
    conn = get_db_connection()
    result = {'duplicado': False, 'msg': ''}
    if nro_sol:
        dup = conn.execute(
            "SELECT id FROM compras_solicitacoes WHERE nro_solicitacao = ? AND status NOT IN ('COMPRADO','CANCELADO')",
            (nro_sol,)
        ).fetchone()
        if dup:
            result = {'duplicado': True, 'msg': f'Nº Solicitação {nro_sol} já está em aberto (ID #{dup["id"]})'}
    if not result['duplicado'] and nro_orc:
        dup = conn.execute(
            "SELECT id FROM compras_solicitacoes WHERE nro_orcamento = ? AND status NOT IN ('COMPRADO','CANCELADO')",
            (nro_orc,)
        ).fetchone()
        if dup:
            result = {'duplicado': True, 'msg': f'Nº Orçamento {nro_orc} já está em aberto (ID #{dup["id"]})'}
    conn.close()
    return jsonify(result)


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)

