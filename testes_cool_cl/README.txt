Bateria de testes da análise léxica e sintática de Cool

Estrutura:
- validos
- erro_sintatico
- erro_lexico

Exemplo de execução:
python parser_cool.py testes_cool/validos/teste_01_minimo.cl

Status esperado:
- arquivos em validos -> sucesso
- arquivos em erro_sintatico -> erro_sintatico
- arquivos em erro_lexico -> erro_lexico
