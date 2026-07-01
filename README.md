# Compilador COOL para Bril

Compilador para a linguagem [COOL](https://en.wikipedia.org/wiki/Cool_(programming_language)) escrito em Python, com backend de geração de código para [Bril](https://capra.cs.cornell.edu/bril/).

## Pipeline

```
.cl  --(lexer_cool.py)-->  tokens
     --(parser_cool.py)-->  AST
     --(semantic_cool.py)-->  AST anotado / erros semânticos
     --(codegen_bril.py)-->  .bril.json
```

## Uso

```bash
python3 lexer_cool.py programa.cl        # análise léxica
python3 parser_cool.py programa.cl       # análise sintática
python3 semantic_cool.py programa.cl     # análise semântica
python3 codegen_bril.py programa.cl      # gera programa.bril.json
```

Para executar o `.bril.json` gerado, use as ferramentas do [Bril](bril/) (ex.: `brili`).

### Argumentos CLI — `codegen_bril.py`

| Argumento | Tipo | Descrição | Exemplo de uso |
|---|---|---|---|
| `arquivo` | posicional | Arquivo COOL (`.cl`) a compilar. Gera um `.bril.json` no mesmo diretório. | `python3 codegen_bril.py exemplo.cl` |
| `--no-print-main` | flag | Suprime o `print` automático do resultado de `Main.main` quando ele retorna `Int` ou `Bool`. | `python3 codegen_bril.py prog.cl --no-print-main` |
| `--strict-semantic` | flag | Aborta a geração se a análise semântica encontrar qualquer erro (padrão: modo permissivo, gera mesmo com avisos). | `python3 codegen_bril.py prog.cl --strict-semantic` |
| `--debug-string-ids` | flag | Para chamadas `out_string` com strings dinâmicas (não literais), imprime o comprimento em vez dos caracteres — útil para depurar sem o ruído visual do print char-a-char. | `python3 codegen_bril.py prog.cl --debug-string-ids` |
| `--main-input` | flag | Faz `@main` receber um parâmetro `int` por chamada `in_int()` encontrada em `Main.main`. Conta as chamadas automaticamente. Executar: `brili prog.bril.json <val1> <val2> ...` | `python3 codegen_bril.py exemplo.cl --main-input` então `brili exemplo.bril.json 42 1 100` |
| `--main-inputs N` | inteiro | Sobrescreve a contagem automática de `in_int()`: força `@main` a ter exatamente `N` parâmetros inteiros. Útil quando as chamadas estão em helpers e não são detectadas automaticamente. | `python3 codegen_bril.py exemplo.cl --main-inputs 3` então `brili exemplo.bril.json 42 1 100` |
| `--main-string STR` | string (repetível) | Injeta uma string fixa no código gerado para ser retornada por `in_string()` em tempo de compilação. Pode ser passado várias vezes; cada ocorrência alimenta a próxima chamada `in_string()`. | `python3 codegen_bril.py pal.cl --main-string "racecar"` então `brili pal.bril.json` |

## Estrutura

- `lexer_cool.py`, `parser_cool.py`, `semantic_cool.py`, `codegen_bril.py` — etapas do compilador
- `bril/` — ferramentas Bril (interpretador, utilitários)
- `chocopy2bril/` — projeto de referência usado como base para o gerador de código
- `cool_testes_semanticos/`, `testes_cool_cl/` — casos de teste (válidos e inválidos)
- `exemplos_codegen/` — exemplos de saída da geração de código
- `CODEGEN_PLAN.md` — plano da etapa de geração de código
