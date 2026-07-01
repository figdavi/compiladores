# Argumentos CLI — `codegen_bril_v4_1.py`

| Argumento | Tipo | Descrição | Exemplo de uso |
|---|---|---|---|
| `arquivo` | posicional | Arquivo COOL (`.cl`) a compilar. Gera um `.bril.json` no mesmo diretório. | `python3 codegen_bril_v4_1.py exemplo.cl` |
| `--no-print-main` | flag | Suprime o `print` automático do resultado de `Main.main` quando ele retorna `Int` ou `Bool`. | `python3 codegen_bril_v4_1.py prog.cl --no-print-main` |
| `--strict-semantic` | flag | Aborta a geração se a análise semântica encontrar qualquer erro (padrão: modo permissivo, gera mesmo com avisos). | `python3 codegen_bril_v4_1.py prog.cl --strict-semantic` |
| `--debug-string-ids` | flag | Para chamadas `out_string` com strings dinâmicas (não literais), imprime o comprimento em vez dos caracteres — útil para depurar sem o ruído visual do print char-a-char. | `python3 codegen_bril_v4_1.py prog.cl --debug-string-ids` |
| `--main-input` | flag | Faz `@main` receber um parâmetro `int` por chamada `in_int()` encontrada em `Main.main`. Conta as chamadas automaticamente. Executar: `brili prog.bril.json <val1> <val2> ...` | `python3 codegen_bril_v4_1.py exemplo.cl --main-input` então `brili exemplo.bril.json 42 1 100` |
| `--main-inputs N` | inteiro | Sobrescreve a contagem automática de `in_int()`: força `@main` a ter exatamente `N` parâmetros inteiros. Útil quando as chamadas estão em helpers e não são detectadas automaticamente. | `python3 codegen_bril_v4_1.py exemplo.cl --main-inputs 3` então `brili exemplo.bril.json 42 1 100` |
| `--main-string STR` | string (repetível) | Injeta uma string fixa no código gerado para ser retornada por `in_string()` em tempo de compilação. Pode ser passado várias vezes; cada ocorrência alimenta a próxima chamada `in_string()`. | `python3 codegen_bril_v4_1.py pal.cl --main-string "racecar"` então `brili pal.bril.json` |