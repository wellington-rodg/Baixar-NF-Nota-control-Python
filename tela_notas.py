"""Tela para escolher o range de notas e baixar os PDFs (usa baixar_notas.py)."""
import os
import queue
import re
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import baixar_notas

PASTA_APP = Path(__file__).parent
RE_DATA = re.compile(r"^\d{2}/\d{2}/\d{4}$")


class Tela:
    def __init__(self, root):
        self.root = root
        self.fila = queue.Queue()
        self.cancelar = threading.Event()
        self.rodando = False

        root.title("Baixar NFS-e - Goiania")
        root.geometry("560x520")
        root.minsize(480, 440)

        frm = ttk.Frame(root, padding=12)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text=f"Empresa: {baixar_notas.CNPJ}", font=("Segoe UI", 10, "bold")).pack(anchor="w")

        # --- Filtro ---
        filtro = ttk.LabelFrame(frm, text="Quais notas", padding=10)
        filtro.pack(fill="x", pady=(10, 0))

        self.modo = tk.StringVar(value="numeros")
        self.num_ini, self.num_fim = tk.StringVar(), tk.StringVar()
        self.dt_ini, self.dt_fim = tk.StringVar(), tk.StringVar()

        ttk.Radiobutton(filtro, text="Faixa de números", variable=self.modo, value="numeros",
                        command=self.trocar_modo).grid(row=0, column=0, sticky="w")
        ttk.Label(filtro, text="de").grid(row=0, column=1, padx=(10, 4))
        self.e_num_ini = ttk.Entry(filtro, textvariable=self.num_ini, width=12)
        self.e_num_ini.grid(row=0, column=2)
        ttk.Label(filtro, text="até").grid(row=0, column=3, padx=4)
        self.e_num_fim = ttk.Entry(filtro, textvariable=self.num_fim, width=12)
        self.e_num_fim.grid(row=0, column=4)

        ttk.Radiobutton(filtro, text="Período (competência)", variable=self.modo, value="periodo",
                        command=self.trocar_modo).grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Label(filtro, text="de").grid(row=1, column=1, padx=(10, 4), pady=(8, 0))
        self.e_dt_ini = ttk.Entry(filtro, textvariable=self.dt_ini, width=12)
        self.e_dt_ini.grid(row=1, column=2, pady=(8, 0))
        ttk.Label(filtro, text="até").grid(row=1, column=3, padx=4, pady=(8, 0))
        self.e_dt_fim = ttk.Entry(filtro, textvariable=self.dt_fim, width=12)
        self.e_dt_fim.grid(row=1, column=4, pady=(8, 0))
        ttk.Label(filtro, text="dd/mm/aaaa", foreground="gray").grid(row=1, column=5, padx=6, pady=(8, 0))

        # --- Pasta ---
        pasta = ttk.LabelFrame(frm, text="Salvar em", padding=10)
        pasta.pack(fill="x", pady=(10, 0))
        self.saida = tk.StringVar(value=str(baixar_notas.PASTA_PADRAO))
        ttk.Entry(pasta, textvariable=self.saida).pack(side="left", fill="x", expand=True)
        ttk.Button(pasta, text="Escolher...", command=self.escolher_pasta).pack(side="left", padx=(6, 0))

        # --- Botoes ---
        botoes = ttk.Frame(frm)
        botoes.pack(fill="x", pady=(12, 0))
        ttk.Button(botoes, text="Abrir Chrome", command=self.abrir_chrome).pack(side="left")
        self.b_baixar = ttk.Button(botoes, text="Baixar PDFs", command=self.iniciar)
        self.b_baixar.pack(side="left", padx=6)
        self.b_parar = ttk.Button(botoes, text="Parar", command=self.cancelar.set, state="disabled")
        self.b_parar.pack(side="left")
        ttk.Button(botoes, text="Abrir pasta", command=self.abrir_pasta).pack(side="right")

        self.barra = ttk.Progressbar(frm, mode="determinate")
        self.barra.pack(fill="x", pady=(10, 0))

        self.log = tk.Text(frm, height=12, state="disabled", font=("Consolas", 9))
        self.log.pack(fill="both", expand=True, pady=(8, 0))

        self.trocar_modo()
        root.after(100, self.processar_fila)

    def trocar_modo(self):
        por_numero = self.modo.get() == "numeros"
        for e in (self.e_num_ini, self.e_num_fim):
            e.configure(state="normal" if por_numero else "disabled")
        for e in (self.e_dt_ini, self.e_dt_fim):
            e.configure(state="disabled" if por_numero else "normal")

    def escolher_pasta(self):
        pasta = filedialog.askdirectory(initialdir=self.saida.get())
        if pasta:
            self.saida.set(pasta)

    def abrir_chrome(self):
        subprocess.Popen(["cmd", "/c", str(PASTA_APP / "abrir_chrome_debug.bat")],
                         creationflags=subprocess.CREATE_NO_WINDOW)

    def abrir_pasta(self):
        pasta = Path(self.saida.get())
        pasta.mkdir(parents=True, exist_ok=True)
        os.startfile(pasta)

    def validar(self):
        if self.modo.get() == "numeros":
            ini, fim = self.num_ini.get().strip(), self.num_fim.get().strip()
            if not (ini.isdigit() and fim.isdigit()):
                raise ValueError("Informe os números inicial e final.")
            if int(ini) > int(fim):
                raise ValueError("O número inicial é maior que o final.")
            return {"numeros": (ini, fim)}
        ini, fim = self.dt_ini.get().strip(), self.dt_fim.get().strip()
        if not (RE_DATA.match(ini) and RE_DATA.match(fim)):
            raise ValueError("Informe as datas no formato dd/mm/aaaa.")
        return {"periodo": (ini, fim)}

    def iniciar(self):
        try:
            filtro = self.validar()
        except ValueError as e:
            messagebox.showwarning("Atenção", str(e))
            return

        self.cancelar.clear()
        self.rodando = True
        self.b_baixar.configure(state="disabled")
        self.b_parar.configure(state="normal")
        self.barra.configure(value=0)
        self.escrever("-" * 50)

        threading.Thread(target=self.trabalhar, args=(filtro,), daemon=True).start()

    def trabalhar(self, filtro):
        try:
            baixar_notas.executar(
                self.saida.get(),
                log=lambda msg: self.fila.put(("log", msg)),
                progresso=lambda i, total: self.fila.put(("progresso", (i, total))),
                cancelar=self.cancelar,
                **filtro,
            )
        except baixar_notas.ErroAutomacao as e:
            self.fila.put(("erro", str(e)))
        except Exception as e:
            self.fila.put(("erro", f"Erro inesperado: {e}"))
        finally:
            self.fila.put(("fim", None))

    def processar_fila(self):
        # A automacao roda em outra thread; a tela so e atualizada aqui
        while not self.fila.empty():
            tipo, valor = self.fila.get()
            if tipo == "log":
                self.escrever(valor)
            elif tipo == "progresso":
                i, total = valor
                self.barra.configure(maximum=total, value=i)
            elif tipo == "erro":
                self.escrever(f"ERRO: {valor}")
                messagebox.showerror("Erro", valor)
            elif tipo == "fim":
                self.rodando = False
                self.b_baixar.configure(state="normal")
                self.b_parar.configure(state="disabled")
        self.root.after(100, self.processar_fila)

    def escrever(self, msg):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")


if __name__ == "__main__":
    root = tk.Tk()
    Tela(root)
    root.mainloop()
