import sqlite3
import csv

def rodar_robo():
    print("Iniciando a importação das especialidades...")
    conn = sqlite3.connect('controle_abastecimento.db')
    cursor = conn.cursor()

    # 1. Tenta adicionar a nova coluna na tabela
    try:
        cursor.execute("ALTER TABLE frotas ADD COLUMN especialidade TEXT DEFAULT 'OUTROS'")
        print("Nova coluna 'especialidade' criada no banco de dados!")
    except sqlite3.OperationalError:
        print("A coluna 'especialidade' já existe. Continuando...")

    # 2. Lê o arquivo CSV com a codificação correta para o Windows/Excel
    sucesso = 0
    try:
        # Mudamos a codificação para 'latin-1' (padrão do Excel)
        with open('dados.csv', 'r', encoding='latin-1') as f:

            # Tenta ler primeiro com ponto e vírgula (padrão do Excel brasileiro)
            leitor = csv.DictReader(f, delimiter=';')

            # Se o cabeçalho 'ID' não for encontrado, significa que ele usou vírgula. Ajustamos automaticamente!
            if not leitor.fieldnames or 'ID' not in leitor.fieldnames:
                f.seek(0)
                leitor = csv.DictReader(f, delimiter=',')

            for linha in leitor:
                id_frota = linha.get('ID', '').strip()
                categoria = linha.get('Categoria', 'OUTROS').strip().upper()

                if id_frota:
                    cursor.execute('UPDATE frotas SET especialidade = ? WHERE id_frota = ?', (categoria, id_frota))
                    sucesso += 1

        conn.commit()
        print(f"SUCESSO! {sucesso} frotas foram atualizadas com as suas especialidades.")
    except Exception as e:
        print(f"Erro ao ler o arquivo dados.csv: {e}")
    finally:
        conn.close()

if __name__ == '__main__':
    rodar_robo()