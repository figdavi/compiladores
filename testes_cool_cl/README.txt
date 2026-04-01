Pasta de testes para o analisador léxico da linguagem Cool.

Estrutura:
- testes_validos/
- testes_invalidos/
- testes_mistos/

Observações:
1. O arquivo i9_booleano_com_inicial_maiuscula.cl não deve gerar erro léxico,
   mas serve para verificar se True/FALSE NÃO viram os tokens booleanos TRUE/FALSE.
2. O caso de caractere nulo real foi deixado em um script Python:
   - exemplo_python_caractere_nulo_real.py
   Isso foi feito porque inserir \x00 diretamente em um arquivo .cl comum pode ser inconveniente.
