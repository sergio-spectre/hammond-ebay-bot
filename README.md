# Monitor de preços — Hammond Collection (eBay → Discord)

Passo a passo completo, do zero, sem precisar usar linha de comando. Segue a
ordem exata.

---

## PARTE 1 — eBay (pegar as chaves de Production)

Você já fez essa parte e me passou as chaves de Production:

- Client ID: `SergioLu-HAMMOND-PRD-3a231bb4d-aedfd0e1`
- Client Secret: `PRD-a231bb4d64d6-bb9f-4dc8-a852-dc56`

Guarde essas duas informações, você vai usar na Parte 3. Não precisa
mexer mais em developer.ebay.com.

---

## PARTE 2 — Criar o repositório no GitHub

1. Acesse https://github.com e crie uma conta gratuita, se ainda não tiver
   (botão "Sign up").
2. Depois de logado, clique no **+** no canto superior direito → **New
   repository**.
3. Em "Repository name", coloque por exemplo `hammond-ebay-bot`.
4. Marque **Private** (para ninguém mais ver seus arquivos).
5. Não marque nenhuma opção de "Add a README" (deixe tudo desmarcado).
6. Clique em **Create repository**.

Você vai cair numa página vazia com instruções de linha de comando. Ignore
essas instruções, vamos fazer tudo pelo navegador.

7. Nessa mesma página, procure o link **"uploading an existing file"**
   (costuma aparecer no meio do texto) e clique nele. Se não aparecer,
   clique em **Add file → Upload files** no canto superior direito.
8. Agora você precisa soltar os arquivos que vieram no zip que te mandei.
   No seu computador, **extraia (descompacte)** o arquivo
   `hammond-ebay-bot.zip` primeiro. Dentro dele tem:
   - `monitor.py`
   - `requirements.txt`
   - `seen_items.json`
   - uma pasta `.github` (com uma subpasta `workflows` dentro, e um arquivo
     `monitor.yml` lá dentro)
   - `README.md` (este arquivo)
9. Arraste **a pasta `.github` inteira** e os arquivos `monitor.py`,
   `requirements.txt`, `seen_items.json`, `README.md` para a área de
   upload do GitHub (a caixa pontilhada escrito "Drag files here").
   O GitHub reconhece a estrutura de pastas automaticamente.
10. Role para baixo e clique em **Commit changes**.

Confira depois se o repositório ficou assim:

```
hammond-ebay-bot/
├── .github/
│   └── workflows/
│       └── monitor.yml
├── monitor.py
├── requirements.txt
├── seen_items.json
└── README.md
```

Se o `monitor.yml` não ficou dentro de `.github/workflows/`, o robô não vai
rodar sozinho. Confira o caminho exato clicando nas pastas.

---

## PARTE 3 — Cadastrar as chaves como "Secrets" do repositório

Isso guarda suas chaves de forma criptografada, sem aparecer pra ninguém.

1. No repositório, clique em **Settings** (aba no topo, ícone de
   engrenagem).
2. No menu da esquerda, clique em **Secrets and variables → Actions**.
3. Clique no botão verde **New repository secret**.
4. Crie o primeiro:
   - Name: `EBAY_CLIENT_ID`
   - Secret: `SergioLu-HAMMOND-PRD-3a231bb4d-aedfd0e1`
   - Clique em **Add secret**.
5. Clique em **New repository secret** de novo e crie o segundo:
   - Name: `EBAY_CLIENT_SECRET`
   - Secret: `PRD-a231bb4d64d6-bb9f-4dc8-a852-dc56`
   - Clique em **Add secret**.
6. Clique em **New repository secret** de novo e crie o terceiro:
   - Name: `DISCORD_WEBHOOK_URL`
   - Secret: a URL do seu webhook do Discord (a que você já me passou)
   - Clique em **Add secret**.

No final você deve ter 3 secrets listados: `EBAY_CLIENT_ID`,
`EBAY_CLIENT_SECRET`, `DISCORD_WEBHOOK_URL`.

---

## PARTE 4 — Ativar e testar

1. No repositório, clique na aba **Actions** (topo da página).
2. Se aparecer uma tela perguntando para confirmar workflows, clique em
   **"I understand my workflows, go ahead and enable them"**.
3. Na lista da esquerda, clique em **"Monitor Hammond Collection no
   eBay"**.
4. Clique no botão **Run workflow** (dropdown cinza do lado direito) →
   confirme clicando em **Run workflow** de novo.
5. Espere uns 10 a 20 segundos e atualize a página. Vai aparecer uma
   execução com uma bolinha amarela (rodando), depois verde (deu certo) ou
   vermelha (deu erro).
6. Clique na execução para ver o log. Se der certo, você já deve ver
   mensagens chegando no seu canal do Discord (se existirem anúncios ativos
   de Hammond Collection no momento).

Se der vermelho (erro), clique em cima do passo **"Run monitor"** para
abrir o log e me manda a mensagem de erro que aparece (pode apagar
qualquer trecho que pareça uma chave/senha antes de me mandar, embora o
script não imprima as chaves).

---

## Pronto, e agora?

A partir daqui o robô roda sozinho a cada 20 minutos, sem você precisar
fazer nada. Você só recebe alerta quando aparece um anúncio novo (a
primeira execução notifica tudo que encontrar, porque ainda não tem
histórico).

## Ajustes que você pode querer fazer depois

Edite `monitor.py` direto pelo site do GitHub (clique no arquivo → ícone de
lápis para editar → Commit changes):

- `SEARCH_QUERY` — termo de busca (hoje: `"Jurassic World Hammond
  Collection"`).
- `MARKETPLACE_ID` — hoje busca no eBay.com (EUA/USD). Para outro site do
  eBay, troque para `EBAY_GB`, `EBAY_DE`, etc.
- `OUTLIER_MULTIPLIER` — hoje ignora anúncios com preço acima de **1,5x a
  mediana** dos anúncios encontrados na mesma busca.
- `SEEN_TTL_DAYS` — por quantos dias o robô "lembra" de um anúncio (hoje:
  45 dias).

O horário do cron em `.github/workflows/monitor.yml` está em **UTC** e o
GitHub Actions não permite intervalos menores que 5 minutos. Não precisa
mexer nisso, já está em 20 minutos.

## Observações importantes

- Agendamentos do GitHub Actions não são "no minuto exato": em picos de
  uso o GitHub pode atrasar alguns minutos. É normal.
- Se o repositório ficar 60 dias sem nenhum commit, o GitHub desativa
  workflows agendados sozinho; se isso acontecer, basta ir na aba Actions
  e reativar.
- A conta de Production do eBay tem limite de chamadas alto (milhares por
  dia); rodando a cada 20 min você usa uma fração mínima disso.
