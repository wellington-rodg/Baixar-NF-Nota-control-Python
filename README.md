# NFS-e Goiânia - download de PDFs

Baixa o PDF (DANFSe) das NFS-e emitidas em notaeletronica.com.br/goiania, um arquivo por nota,
no formato `NFSe_<numero>_<data>_<tomador>.pdf`.

## Instalação

```
pip install -r requirements.txt
```

Não precisa de `playwright install`: o script se conecta a um Chrome já aberto.

## Uso

1. Abra o Chrome com `abrir_chrome_debug.bat` (perfil próprio, porta de depuração 9222).
2. Nesse Chrome, passe pela verificação do Cloudflare e faça login no site.
3. Abra a tela com `Baixar Notas.bat`, ou use a linha de comando:

```
python baixar_notas.py --numeros 19070-19077
python baixar_notas.py --periodo 01/10/2026 31/10/2026
```

Os PDFs vão para `notas_pdf/`. Notas que já estão na pasta são puladas.

## Como funciona

- Seleciona a empresa pelo CNPJ (`CNPJ` em `baixar_notas.py`) e abre "Consultar / Cancelar NFS-e".
- Baixa em lotes de 10 notas pelo botão "Exportar todas as notas em .PDF" e separa o PDF
  em um arquivo por nota, para fazer poucas requisições ao site.
- Para se um lote falhar ou após 3 erros seguidos.
- Nunca aciona os botões de cancelar ou substituir nota.

Enquanto roda, não feche o Chrome de automação nem mexa na aba do site.
