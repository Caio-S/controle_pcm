import glob
import json
import os
import re
import sqlite3
import sys
import zipfile
from datetime import datetime, timedelta
from xml.etree import ElementTree as ET


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "controle_abastecimento.db")
DEFAULT_PATTERN = os.path.join(
    BASE_DIR, "Or*amento_reforma_safra_26_Oficial_-_Copia.xlsx"
)
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
RID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
EXCEL_EPOCH = datetime(1899, 12, 30)


def col_to_num(ref):
    match = re.match(r"([A-Z]+)", ref)
    total = 0
    for char in match.group(1):
        total = total * 26 + ord(char) - 64
    return total


def serial_to_date(value, with_time=False):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value or "").strip()
    if number <= 0:
        return ""
    dt = EXCEL_EPOCH + timedelta(days=number)
    if with_time and abs(number - int(number)) > 0:
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    return dt.strftime("%Y-%m-%d")


def month_from_serial(value):
    dt = EXCEL_EPOCH + timedelta(days=float(value))
    return dt.year, dt.month, dt.strftime("%Y-%m-%d")


def to_float(value):
    if value is None:
        return None
    text = str(value).strip().replace(",", ".")
    if not text or text in {"\\N", "#N/A", "#VALUE!"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def clean_text(value):
    text = str(value or "").strip()
    return "" if text in {"\\N", "#N/A", "#VALUE!"} else text


def normalize_status(value):
    status = clean_text(value).upper()
    if status == "REFROMA":
        return "REFORMA"
    if status in {"REFORMA", "LINEAR", "AGUARDANDO", "CONCLUIDA"}:
        return status
    return ""


class WorkbookReader:
    def __init__(self, path):
        self.path = path
        self.zip = zipfile.ZipFile(path)
        self.shared = self._read_shared_strings()
        self.sheets = self._read_sheets()

    def _read_shared_strings(self):
        if "xl/sharedStrings.xml" not in self.zip.namelist():
            return []
        root = ET.fromstring(self.zip.read("xl/sharedStrings.xml"))
        return ["".join(t.text or "" for t in si.iter(NS + "t")) for si in root.findall(NS + "si")]

    def _read_sheets(self):
        workbook = ET.fromstring(self.zip.read("xl/workbook.xml"))
        rels_root = ET.fromstring(self.zip.read("xl/_rels/workbook.xml.rels"))
        rels = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels_root}
        sheets = []
        for sheet in workbook.find(NS + "sheets"):
            sheets.append(
                {
                    "name": sheet.attrib["name"],
                    "state": sheet.attrib.get("state", "visible"),
                    "path": "xl/" + rels[sheet.attrib[RID]],
                }
            )
        return sheets

    def cell_value(self, cell):
        value_el = cell.find(NS + "v")
        raw = value_el.text if value_el is not None else ""
        if cell.attrib.get("t") == "s" and raw != "":
            return self.shared[int(raw)]
        inline = cell.find(NS + "is")
        if inline is not None:
            return "".join(t.text or "" for t in inline.iter(NS + "t"))
        return raw

    def rows(self, sheet_name):
        sheet = next(s for s in self.sheets if s["name"] == sheet_name)
        root = ET.fromstring(self.zip.read(sheet["path"]))
        for row in root.findall(NS + "sheetData/" + NS + "row"):
            values = {}
            for cell in row.findall(NS + "c"):
                value = self.cell_value(cell)
                if value != "":
                    values[col_to_num(cell.attrib["r"])] = value
            yield int(row.attrib["r"]), values


def create_tables(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS reforma_importacoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            arquivo TEXT NOT NULL,
            data_importacao TEXT NOT NULL,
            total_abas INTEGER DEFAULT 0,
            total_linhas INTEGER DEFAULT 0,
            observacao TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS reforma_planilha_linhas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            importacao_id INTEGER NOT NULL,
            aba TEXT NOT NULL,
            linha INTEGER NOT NULL,
            dados_json TEXT NOT NULL,
            UNIQUE(importacao_id, aba, linha)
        );
        CREATE TABLE IF NOT EXISTS reforma_maquinas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_frota TEXT NOT NULL,
            ano_fabricacao TEXT DEFAULT '',
            km_horimetro TEXT DEFAULT '',
            dt_ultima_reforma TEXT DEFAULT '',
            observacao TEXT DEFAULT '',
            UNIQUE(id_frota)
        );
        CREATE TABLE IF NOT EXISTS reforma_programacao (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_frota TEXT NOT NULL,
            ano INTEGER NOT NULL,
            mes INTEGER NOT NULL,
            status TEXT DEFAULT 'PENDENTE',
            observacao TEXT DEFAULT '',
            UNIQUE(id_frota, ano, mes)
        );
        CREATE TABLE IF NOT EXISTS reforma_planejamento_planilha (
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
            UNIQUE(importacao_id, aba, id_frota, ano, mes, status)
        );
        CREATE TABLE IF NOT EXISTS reforma_os_planilha (
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
            UNIQUE(importacao_id, aba, documento, id_frota)
        );
        CREATE TABLE IF NOT EXISTS reforma_gastos_planilha (
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
            UNIQUE(importacao_id, aba, id_frota, ano, mes, valor)
        );
        CREATE TABLE IF NOT EXISTS reforma_orcamento_planilha (
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
            UNIQUE(importacao_id, aba, id_frota, componente)
        );
        """
    )


def import_raw_rows(conn, wb, import_id):
    total = 0
    for sheet in wb.sheets:
        for row_num, row in wb.rows(sheet["name"]):
            if not row:
                continue
            data = {str(col): value for col, value in sorted(row.items())}
            conn.execute(
                """
                INSERT OR IGNORE INTO reforma_planilha_linhas
                    (importacao_id, aba, linha, dados_json)
                VALUES (?, ?, ?, ?)
                """,
                (import_id, sheet["name"], row_num, json.dumps(data, ensure_ascii=False)),
            )
            total += 1
    return total


def import_base_frota(conn, wb):
    imported = 0
    for _, row in wb.rows("BASE FROTA"):
        code = clean_text(row.get(4))
        if not code or code == "CodFrota":
            continue
        desc = clean_text(row.get(5)) or clean_text(row.get(7)) or "SEM DESCRICAO"
        ano = clean_text(row.get(9))
        km = clean_text(row.get(23))
        especialidade = clean_text(row.get(27)) or "OUTROS"
        agrupamento = clean_text(row.get(28)) or clean_text(row.get(29)) or "GERAL"
        conn.execute(
            """
            INSERT INTO frotas (id_frota, descricao, setor, especialidade, agrupamento)
            VALUES (?, ?, '', ?, ?)
            ON CONFLICT(id_frota) DO UPDATE SET
                descricao = CASE WHEN descricao IS NULL OR TRIM(descricao) = '' OR descricao = 'SEM DESCRICAO' THEN excluded.descricao ELSE descricao END,
                especialidade = CASE WHEN especialidade IS NULL OR TRIM(especialidade) = '' OR especialidade = 'OUTROS' THEN excluded.especialidade ELSE especialidade END,
                agrupamento = CASE WHEN agrupamento IS NULL OR TRIM(agrupamento) = '' OR agrupamento = 'GERAL' THEN excluded.agrupamento ELSE agrupamento END
            """,
            (code, desc, especialidade, agrupamento),
        )
        conn.execute(
            """
            INSERT INTO reforma_maquinas (id_frota, ano_fabricacao, km_horimetro)
            VALUES (?, ?, ?)
            ON CONFLICT(id_frota) DO UPDATE SET
                ano_fabricacao = CASE WHEN TRIM(ano_fabricacao) = '' THEN excluded.ano_fabricacao ELSE ano_fabricacao END,
                km_horimetro = CASE WHEN TRIM(km_horimetro) = '' THEN excluded.km_horimetro ELSE km_horimetro END
            """,
            (code, ano, km),
        )
        imported += 1
    return imported


def import_planning(conn, wb, import_id):
    configs = {
        "CAMINH plan": {"header": 2, "start": 3, "group": 1, "esp": 2, "code": 3, "ano": 4, "km": 5, "ult": 6},
        "TRATOR PLAN": {"header": 2, "start": 3, "group": 1, "esp": 2, "code": 3, "ano": 4, "km": 5, "ult": 6},
        "colhedora plan": {"header": 3, "start": 4, "group": None, "esp": 1, "code": 2, "ano": 3, "km": 4, "ult": 5},
    }
    imported = 0
    for sheet, cfg in configs.items():
        all_rows = dict(wb.rows(sheet))
        header = all_rows.get(cfg["header"], {})
        month_cols = {}
        for col, value in header.items():
            try:
                if float(value) > 40000:
                    month_cols[col] = month_from_serial(value)
            except (TypeError, ValueError):
                pass
        for row_num, row in sorted(all_rows.items()):
            if row_num < cfg["start"]:
                continue
            code = clean_text(row.get(cfg["code"]))
            if not code:
                continue
            agrup = clean_text(row.get(cfg["group"])) if cfg["group"] else "COLHEDORA"
            esp = clean_text(row.get(cfg["esp"]))
            ano_fab = clean_text(row.get(cfg["ano"]))
            km = clean_text(row.get(cfg["km"]))
            ult = clean_text(row.get(cfg["ult"]))
            if ult and re.match(r"^\d+(\.\d+)?$", ult):
                ult = serial_to_date(ult, with_time=True)
            conn.execute(
                """
                INSERT INTO frotas (id_frota, descricao, setor, especialidade, agrupamento)
                VALUES (?, 'SEM DESCRICAO', '', ?, ?)
                ON CONFLICT(id_frota) DO UPDATE SET
                    especialidade = CASE WHEN especialidade IS NULL OR TRIM(especialidade) = '' OR especialidade = 'OUTROS' THEN excluded.especialidade ELSE especialidade END,
                    agrupamento = CASE WHEN agrupamento IS NULL OR TRIM(agrupamento) = '' OR agrupamento = 'GERAL' THEN excluded.agrupamento ELSE agrupamento END
                """,
                (code, esp or "OUTROS", agrup or "GERAL"),
            )
            conn.execute(
                """
                INSERT INTO reforma_maquinas (id_frota, ano_fabricacao, km_horimetro, dt_ultima_reforma)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id_frota) DO UPDATE SET
                    ano_fabricacao = CASE WHEN TRIM(ano_fabricacao) = '' THEN excluded.ano_fabricacao ELSE ano_fabricacao END,
                    km_horimetro = CASE WHEN TRIM(km_horimetro) = '' THEN excluded.km_horimetro ELSE km_horimetro END,
                    dt_ultima_reforma = CASE WHEN TRIM(dt_ultima_reforma) = '' THEN excluded.dt_ultima_reforma ELSE dt_ultima_reforma END
                """,
                (code, ano_fab, km, ult),
            )
            for col, (year, month, date_text) in month_cols.items():
                status = normalize_status(row.get(col))
                if not status:
                    continue
                conn.execute(
                    """
                    INSERT OR IGNORE INTO reforma_planejamento_planilha
                        (importacao_id, aba, id_frota, agrupamento, especialidade, ano_fabricacao,
                         km_horimetro, dt_ultima_reforma, data_programada, ano, mes, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (import_id, sheet, code, agrup, esp, ano_fab, km, ult, date_text, year, month, status),
                )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO reforma_programacao
                        (id_frota, ano, mes, status, observacao)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (code, year, month, status, "Importado da planilha"),
                )
                imported += 1
    return imported


def import_os(conn, wb, import_id):
    imported = 0
    for sheet in ("O.S ABERTA", "Planilha13"):
        rows = dict(wb.rows(sheet))
        header = {col: clean_text(value) for col, value in rows.get(1, {}).items()}
        for row_num, row in rows.items():
            if row_num == 1:
                continue
            data = {header.get(col, str(col)): value for col, value in row.items() if header.get(col, str(col))}
            frota = clean_text(data.get("codigo_frota") or row.get(1) or row.get(11))
            documento = clean_text(data.get("documento"))
            if not frota and not documento:
                continue
            conn.execute(
                """
                INSERT OR IGNORE INTO reforma_os_planilha
                    (importacao_id, aba, documento, id_frota, empresa, status_os, data_abertura,
                     data_liberacao, tipo_manutencao, centro_custo, especialidade, agrupamento,
                     descricao_frota, dados_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    import_id,
                    sheet,
                    documento,
                    frota,
                    clean_text(data.get("descricao_empresa")),
                    clean_text(data.get("descricao_status")),
                    serial_to_date(data.get("data_hora_abertura"), with_time=True),
                    serial_to_date(data.get("data_hora_liberacao"), with_time=True),
                    clean_text(data.get("descricao_tipo_manutencao")),
                    clean_text(data.get("descricao_centro_custo")),
                    clean_text(data.get("descricao_especialidade_frota")),
                    clean_text(data.get("descricao_especialidadeAgrup")),
                    clean_text(data.get("descricao_frota")),
                    json.dumps(data, ensure_ascii=False),
                ),
            )
            imported += 1
    return imported


def import_gastos(conn, wb, import_id):
    imported = 0
    for _, row in wb.rows("base reforma"):
        code = clean_text(row.get(1))
        year = to_float(row.get(4))
        month = to_float(row.get(5))
        value = to_float(row.get(6))
        if not code or not year or not month or value is None:
            continue
        conn.execute(
            """
            INSERT OR IGNORE INTO reforma_gastos_planilha
                (importacao_id, aba, id_frota, descricao_frota, empresa, ano, mes, valor)
            VALUES (?, 'base reforma', ?, ?, ?, ?, ?, ?)
            """,
            (import_id, code, clean_text(row.get(2)), clean_text(row.get(3)), int(year), int(month), value),
        )
        imported += 1
    for _, row in wb.rows("Gasto Reforma Anterior"):
        code = clean_text(row.get(17))
        value = to_float(row.get(19))
        if not code or value is None:
            continue
        conn.execute(
            """
            INSERT OR IGNORE INTO reforma_gastos_planilha
                (importacao_id, aba, id_frota, descricao_frota, empresa, ano, mes, especialidade, valor)
            VALUES (?, 'Gasto Reforma Anterior', ?, ?, ?, 0, 0, ?, ?)
            """,
            (import_id, code, clean_text(row.get(2)), clean_text(row.get(16)), clean_text(row.get(18)), value),
        )
        imported += 1
    return imported


def import_orcamentos(conn, wb, import_id):
    imported = 0
    budget_sheets = [
        "Base Diversos",
        "Base Colhedora",
        "Base Trator Esteira",
        "trator 25",
        "caminhões cana 25",
        "orç colh. 23-24",
        "cam. assis. 23-24",
        "carretel",
        "caçamba 23-24",
        "comboio 23-24",
        "pipa 23-24",
        "tanque",
        "reboque cana. 23-24",
    ]
    for sheet in budget_sheets:
        if sheet not in {s["name"] for s in wb.sheets}:
            continue
        rows = dict(wb.rows(sheet))
        header_row_num = min(rows)
        headers = {col: clean_text(value) for col, value in rows.get(header_row_num, {}).items()}
        for row_num, row in rows.items():
            if row_num <= header_row_num:
                continue
            code = clean_text(row.get(2))
            if not code or not re.match(r"^\d+$", code):
                continue
            modelo = clean_text(row.get(1))
            desc = clean_text(row.get(3))
            ano = clean_text(row.get(4))
            for col, component in headers.items():
                if col <= 4 or not component:
                    continue
                value = to_float(row.get(col))
                if value is None or value == 0:
                    continue
                conn.execute(
                    """
                    INSERT OR IGNORE INTO reforma_orcamento_planilha
                        (importacao_id, aba, id_frota, modelo, descricao_frota, ano_fabricacao, componente, valor)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (import_id, sheet, code, modelo, desc, ano, component, value),
                )
                imported += 1
    return imported


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else None
    if not path:
        matches = glob.glob(DEFAULT_PATTERN)
        if not matches:
            raise SystemExit("Planilha nao encontrada em Downloads.")
        path = matches[0]

    wb = WorkbookReader(path)
    conn = sqlite3.connect(DB_PATH)
    try:
        create_tables(conn)
        cur = conn.execute(
            """
            INSERT INTO reforma_importacoes (arquivo, data_importacao, observacao)
            VALUES (?, ?, ?)
            """,
            (path, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "Importacao inicial da planilha oficial"),
        )
        import_id = cur.lastrowid
        raw = import_raw_rows(conn, wb, import_id)
        base = import_base_frota(conn, wb)
        planning = import_planning(conn, wb, import_id)
        os_count = import_os(conn, wb, import_id)
        gastos = import_gastos(conn, wb, import_id)
        orcamentos = import_orcamentos(conn, wb, import_id)
        conn.execute(
            """
            UPDATE reforma_importacoes
               SET total_abas = ?, total_linhas = ?,
                   observacao = ?
             WHERE id = ?
            """,
            (
                len(wb.sheets),
                raw,
                f"base_frota={base}; planejamento={planning}; os={os_count}; gastos={gastos}; orcamentos={orcamentos}",
                import_id,
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    print(f"Importacao #{import_id} concluida")
    print(f"Abas preservadas: {len(wb.sheets)}")
    print(f"Linhas brutas preservadas: {raw}")
    print(f"Frotas/base importadas: {base}")
    print(f"Programacoes importadas: {planning}")
    print(f"Ordens importadas: {os_count}")
    print(f"Gastos importados: {gastos}")
    print(f"Orcamentos/componentes importados: {orcamentos}")


if __name__ == "__main__":
    main()
