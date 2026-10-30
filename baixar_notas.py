"""
Baixa o PDF (DANFSe) das NFS-e emitidas em notaeletronica.com.br/goiania,
um arquivo por nota.

Pre-requisito: Chrome aberto pelo abrir_chrome_debug.bat e com login feito no site.

Exemplos:
    python baixar_notas.py --numeros 19070-19077
    python baixar_notas.py --periodo 01/10/2026 31/10/2026
"""
import argparse
import base64
import io
import re
from pathlib import Path

from playwright.sync_api import sync_playwright
from pypdf import PdfReader, PdfWriter

CDP_URL = "http://127.0.0.1:9222"
CNPJ = "10.360.802/0001-66"
BASE = "https://www.notaeletronica.com.br/goiania"
PASTA_PADRAO = Path(__file__).parent / "notas_pdf"

# As notas sao baixadas em lotes (um PDF com varias notas, separado aqui)
# para fazer poucas requisicoes ao site.
# O site leva ~1,5 s por nota para gerar o PDF (10 notas ~ 15 s).
TAMANHO_LOTE = 10
TEMPO_GERAR_PDF_MS = 180000
MAX_ERROS_SEGUIDOS = 3
RE_NUMERO_NOTA = re.compile(r"N[ÚU]MERO DA NFS-E\s+(\d+)", re.I)
RE_TOMADOR = re.compile(r"TOMADOR / ADQUIRENTE.*?Nome / Nome Empresarial[ \t]*\n([^\n]+)", re.S)



class ErroAutomacao(Exception):
    pass


# Botoes que o script aciona. Nunca acionar btnCancelar / btnSubstituir.
JS_ALVO_POSTBACK = r"""el => (el.getAttribute('href').match(/__doPostBack\('([^']+)'/) || [])[1] || null"""


def postback(frame, alvo, timeout=60000):
    """Dispara um __doPostBack do ASP.NET e espera a pagina recarregar."""
    with frame.expect_navigation(timeout=timeout):
        frame.evaluate("t => setTimeout(() => __doPostBack(t, ''), 0)", alvo)
    frame.wait_for_load_state()


def pagina_do_site(context):
    for p in context.pages:
        if "notaeletronica.com.br/goiania" in p.url and "NotasPdf" not in p.url:
            return p
    raise ErroAutomacao("Nenhuma aba do notaeletronica.com.br aberta. Abra o site e faca login no Chrome de automacao.")


def selecionar_empresa(page):
    if "login" in page.url.lower():
        raise ErroAutomacao("A aba esta na tela de login. Faca login no site e tente de novo.")

    if "empresas.aspx" not in page.url.lower():
        # Volta para a lista de empresas para garantir que o CNPJ certo fique selecionado
        postback(page.main_frame, "lkbAlterarEmpresa")
        page.wait_for_url(re.compile(r"empresas\.aspx", re.I))

    alvo = page.evaluate(
        r"""cnpj => {
            for (const r of document.querySelectorAll('tr')) {
                if (r.cells.length > 2 && [...r.cells].some(c => c.innerText.trim() === cnpj)) {
                    const a = r.querySelector("a[href*='imbSelecione']");
                    if (a) return a.getAttribute('href').match(/__doPostBack\('([^']+)'/)[1];
                }
            }
            return null;
        }""",
        CNPJ,
    )
    if not alvo:
        raise ErroAutomacao(f"CNPJ {CNPJ} nao encontrado na lista de empresas.")
    postback(page.main_frame, alvo)
    page.wait_for_url(re.compile(r"Master2\.aspx", re.I))


def abrir_consulta(page):
    page.wait_for_load_state()
    page.click("a[href$='consulta_nota.aspx']")
    # A consulta carrega dentro de um iframe do painel
    for _ in range(60):
        for frame in page.frames:
            if "consulta_nota.aspx" in frame.url.lower():
                frame.wait_for_selector("#txtNumIni")
                return frame
        page.wait_for_timeout(500)
    raise ErroAutomacao("A tela de consulta nao abriu.")


def buscar(frame, num_ini="", num_fim="", dt_ini="", dt_fim=""):
    if (dt_ini or dt_fim) and not frame.locator("#txtDtEmissaoIni").is_visible():
        postback(frame, "imbArrow")  # abre os "Filtros Adicionais"

    frame.evaluate(
        """v => {
            for (const [id, valor] of Object.entries(v)) {
                const el = document.getElementById(id);
                if (el) el.value = valor;
            }
        }""",
        {"txtNumIni": num_ini, "txtNumFinal": num_fim,
         "txtDtEmissaoIni": dt_ini, "txtDtEmissaoFim": dt_fim},
    )
    postback(frame, "btnLocalizar2")


def ler_pagina(frame):
    return frame.evaluate(
        r"""() => {
            const grid = document.getElementById('dgDocumentos');
            if (!grid) return [];
            return [...grid.rows]
                .filter(r => r.cells.length > 8 && /^\d+$/.test(r.cells[0].innerText.trim()))
                .map(r => ({
                    numero: r.cells[0].innerText.trim(),
                    data: r.cells[5].innerText.trim(),
                    status: r.cells[8].innerText.trim(),
                }));
        }"""
    )


def proxima_pagina(frame):
    """Alvo do postback do link logo depois da pagina atual no paginador (numero ou '...')."""
    return frame.evaluate(
        r"""() => {
            const grid = document.getElementById('dgDocumentos');
            if (!grid) return null;
            const atual = grid.rows[grid.rows.length - 1].cells[0].querySelector('span');
            let el = atual && atual.nextElementSibling;
            while (el && el.tagName !== 'A') el = el.nextElementSibling;
            if (!el) return null;
            const m = el.getAttribute('href').match(/__doPostBack\('([^']+)'/);
            return m ? m[1] : null;
        }"""
    )


def listar_notas(frame):
    notas = ler_pagina(frame)
    while alvo := proxima_pagina(frame):
        postback(frame, alvo)
        notas += ler_pagina(frame)
    return notas


def baixar_pdf(context, frame, numero):
    buscar(frame, numero, numero)
    if [n["numero"] for n in ler_pagina(frame)] != [numero]:
        raise RuntimeError("a busca pela nota nao retornou exatamente essa nota")
    return pdf_da_busca(context, frame)


def separar_por_nota(dados):
    """Divide o PDF de um lote em {numero: bytes do PDF daquela nota}."""
    paginas_por_nota = {}
    atual = None
    for pagina in PdfReader(io.BytesIO(dados)).pages:
        m = RE_NUMERO_NOTA.search(pagina.extract_text() or "")
        if m:
            atual = str(int(m.group(1)))
        if atual is None:
            raise RuntimeError("a primeira pagina do lote nao tem numero de nota")
        # Pagina sem numero e continuacao da nota anterior
        paginas_por_nota.setdefault(atual, []).append(pagina)

    resultado = {}
    for numero, paginas in paginas_por_nota.items():
        escritor = PdfWriter()
        for pagina in paginas:
            escritor.add_page(pagina)
        buf = io.BytesIO()
        escritor.write(buf)
        resultado[numero] = buf.getvalue()
    return resultado


def tomador_do_pdf(dados):
    """Nome do tomador impresso na DANFSe, pronto para usar em nome de arquivo."""
    m = RE_TOMADOR.search(PdfReader(io.BytesIO(dados)).pages[0].extract_text() or "")
    if not m or m.group(1).strip() in ("", "-"):
        return ""
    nome = re.sub(r'[\\/:*?"<>|]', " ", m.group(1))
    return re.sub(r"\s+", " ", nome).strip(" .")[:60]


def nome_arquivo(nota, tomador):
    dia, mes, ano = nota["data"].split("/")
    partes = [f"NFSe_{nota['numero']}", f"{ano}-{mes}-{dia}"]
    if tomador:
        partes.append(tomador)
    if nota["status"].lower().startswith("cancel"):
        partes.append("CANCELADA")
    return "_".join(partes) + ".pdf"


def pdf_da_busca(context, frame):
    """Aciona 'Exportar todas as notas em .PDF' e devolve o PDF da busca atual."""
    botao = frame.locator("a[id$='_btnGerarNotasPdf']")
    postback(frame, botao.evaluate(JS_ALVO_POSTBACK), timeout=TEMPO_GERAR_PDF_MS)
    fechar_popups(context)

    resp = frame.evaluate(
        """async () => {
            const r = await fetch('NotasPdf.aspx', {credentials: 'same-origin'});
            const buf = new Uint8Array(await r.arrayBuffer());
            let s = '';
            for (let i = 0; i < buf.length; i += 0x8000)
                s += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
            return {status: r.status, b64: btoa(s)};
        }"""
    )
    fechar_popups(context)
    dados = base64.b64decode(resp["b64"])
    if resp["status"] != 200 or not dados.startswith(b"%PDF"):
        raise RuntimeError(f"resposta nao e PDF (HTTP {resp['status']})")
    return dados


def bloquear_popup_pdf(route):
    # Depois do "Gerar PDF" o site abre um pop-up em NotasPdf.aspx, que consome o PDF
    # guardado na sessao. Barra so a navegacao do pop-up; o fetch do script passa.
    if route.request.resource_type == "document":
        route.abort()
    else:
        route.continue_()


def fechar_popups(context):
    # Bloqueado, o pop-up vira uma pagina de erro (chrome-error://)
    for p in context.pages:
        if "NotasPdf" in p.url or p.url.startswith("chrome-error://"):
            p.close()


def executar(saida, numeros=None, periodo=None, log=print, progresso=None, cancelar=None):
    """Baixa os PDFs. numeros=(ini, fim) ou periodo=(dd/mm/aaaa, dd/mm/aaaa)."""
    saida = Path(saida)
    saida.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.connect_over_cdp(CDP_URL)
        except Exception:
            raise ErroAutomacao("Nao conectei no Chrome. Abra-o pelo abrir_chrome_debug.bat.")
        context = browser.contexts[0]
        page = pagina_do_site(context)
        fechar_popups(context)
        context.route("**/NotaDigital/NotasPdf.aspx*", bloquear_popup_pdf)

        log(f"Selecionando a empresa {CNPJ}...")
        selecionar_empresa(page)
        frame = abrir_consulta(page)

        dt_ini, dt_fim = periodo or ("", "")
        if numeros:
            buscar(frame, str(numeros[0]), str(numeros[1]))
        else:
            buscar(frame, dt_ini=dt_ini, dt_fim=dt_fim)

        notas = listar_notas(frame)
        log(f"{len(notas)} nota(s) encontrada(s).")

        def ja_existe(nota):
            return any(saida.glob(f"NFSe_{nota['numero']}_*.pdf"))

        pendentes = sorted((n for n in notas if not ja_existe(n)), key=lambda n: int(n["numero"]))
        puladas = len(notas) - len(pendentes)
        if puladas:
            log(f"{puladas} ja existiam na pasta.")

        baixadas = erros = erros_seguidos = 0
        for k in range(0, len(pendentes), TAMANHO_LOTE):
            if cancelar and cancelar.is_set():
                log("Interrompido.")
                break
            lote = pendentes[k:k + TAMANHO_LOTE]
            ini, fim = lote[0]["numero"], lote[-1]["numero"]
            log(f"Lote {ini} a {fim} ({len(lote)} notas)...")

            try:
                # Mantem o filtro de data junto da faixa de numeros
                buscar(frame, ini, fim, dt_ini, dt_fim)
                pdfs = separar_por_nota(pdf_da_busca(context, frame))
            except Exception as e:
                # Nao insiste: um lote com falha costuma indicar problema no site ou na sessao
                log(f"  ERRO no lote: {str(e).splitlines()[0]}")
                log("Execucao interrompida. Tente de novo mais tarde; as notas ja salvas serao puladas.")
                erros += len(lote)
                break

            for nota in lote:
                try:
                    # Nota que nao veio no PDF do lote e baixada sozinha
                    dados = pdfs.get(nota["numero"]) or baixar_pdf(context, frame, nota["numero"])
                    arquivo = saida / nome_arquivo(nota, tomador_do_pdf(dados))
                    arquivo.write_bytes(dados)
                    baixadas += 1
                    erros_seguidos = 0
                    log(f"  ok   {arquivo.name}")
                except Exception as e:
                    erros += 1
                    erros_seguidos += 1
                    log(f"  ERRO nota {nota['numero']}: {str(e).splitlines()[0]}")
                    if erros_seguidos >= MAX_ERROS_SEGUIDOS:
                        break
            if erros_seguidos >= MAX_ERROS_SEGUIDOS:
                log(f"{MAX_ERROS_SEGUIDOS} erros seguidos; execucao interrompida.")
                break
            if progresso:
                progresso(puladas + k + len(lote), len(notas))

        # O ultimo pop-up pode abrir um pouco depois do download
        page.wait_for_timeout(1500)
        fechar_popups(context)
        context.unroute("**/NotaDigital/NotasPdf.aspx*", bloquear_popup_pdf)
        log(f"\nBaixadas: {baixadas} | Ja existiam: {puladas} | Erros: {erros}")
        log(f"Pasta: {saida}")


def main():
    ap = argparse.ArgumentParser(description="Baixa os PDFs das NFS-e de Goiania.")
    grupo = ap.add_mutually_exclusive_group(required=True)
    grupo.add_argument("--numeros", metavar="INI-FIM", help="faixa de numeros, ex: 19070-19077")
    grupo.add_argument("--periodo", nargs=2, metavar=("DD/MM/AAAA", "DD/MM/AAAA"),
                       help="data de competencia inicial e final")
    ap.add_argument("--saida", type=Path, default=PASTA_PADRAO, help="pasta onde salvar os PDFs")
    args = ap.parse_args()

    numeros = [n.strip() for n in args.numeros.split("-")] if args.numeros else None
    try:
        executar(args.saida, numeros=numeros, periodo=args.periodo)
    except ErroAutomacao as e:
        raise SystemExit(str(e))


if __name__ == "__main__":
    main()
