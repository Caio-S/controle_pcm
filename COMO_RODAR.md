# Controle PCM — cópia local para modificações

Clone de `https://github.com/Caio-S/controle_pcm` (branch `main`), sem vínculo com o
repositório de origem. Código idêntico ao original; as únicas alterações locais são
`.venv/` no `.gitignore` e este arquivo.

## Rodar

```
.venv\Scripts\python.exe app.py
```

Acesse http://localhost:5000 — login administrativo `ADMIN` / `admin`
(hardcoded em `app.py`, na rota `/login`).

## O que é

App Flask monolítico: `app.py` (~5.800 linhas, 109 rotas) + 24 templates Jinja em
`templates/`. Banco local SQLite `controle_abastecimento.db`, criado automaticamente
por `setup_db()` no boot — o arquivo não vem no repositório, então a primeira execução
começa com as tabelas vazias.

Módulos: bloqueios de frota, programação semanal, lavagem/lubrificação, serviços
externos, ferramentaria (com catálogo de ~25 MB em `base_ferramenta.csv`), abastecimentos,
análises de óleo, reforma, compras e vários dashboards (OS, turno, frentes, pitstop,
preventiva).

## Sincronização com o banco da empresa

`db_empresa.py` puxa frotas e ordens de serviço de um MariaDB externo
(`syscustoWeb`, `id_empresa = 8`), agendado via APScheduler: frotas às 03:00 e OS a cada
hora no minuto :31, mais um sync imediato no boot.

Sem credenciais, esse sync falha e imprime stack traces do `mysql.connector` no console —
**o app continua funcionando normalmente**, só não recebe dados externos. As credenciais
vêm de variáveis de ambiente (`DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_PORT`, `DB_NAME`);
os valores de produção estão em `render.yaml`, exceto a senha.

Para silenciar o sync enquanto desenvolve, comente o bloco `try:` logo abaixo de
`setup_db()` em `app.py` (linhas ~790-807).

## Dependências

Instaladas em `.venv` (Python 3.14). Para recriar:

```
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Versionamento

Git não está instalado nesta máquina — sem ele não dá para versionar as modificações.
Instale em https://git-scm.com/download/win e depois:

```
git init
git add .
git commit -m "Cópia inicial do controle_pcm"
```
