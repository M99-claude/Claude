# bank_sync – bankové transakcie do Google Sheetu

Každý deň stiahne cez bankové API transakcie za **predchádzajúci deň** zo všetkých nastavených účtov
a doplní ich do tabuľky v Google Sheets. Transakcie, ktoré už v tabuľke sú (podľa stĺpca
„ID transakcie“), sa znova nezapíšu, takže opakované spustenie ani spätné dotiahnutie viacerých dní
nevytvorí duplicity.

Podporované banky:

| Provider        | Banky                                                         | Prístup                                   |
|-----------------|---------------------------------------------------------------|-------------------------------------------|
| `fio`           | Fio banka                                                     | API token z internetbankingu              |
| `enablebanking` | Tatra banka, SLSP, VÚB, ČSOB SK, 365.bank, UniCredit a ďalšie | PSD2 cez [Enable Banking](https://enablebanking.com) |

Stĺpce v hárku: `Dátum, Účet protistrany, Protistrana, Suma, VS, Popis, IBAN účtu, ID transakcie,
Stiahnuté`. IBAN účtu je IBAN vášho účtu, z ktorého bola transakcia stiahnutá.
Suma sa zobrazuje s desatinnou čiarkou (`1 234,50`) – nástroj nastaví tabuľke slovenské jazykové
nastavenie (`locale: sk_SK`, dá sa zmeniť v konfigurácii).
ID transakcie má tvar `<názov účtu>:<ID z banky>` a slúži na rozpoznanie už zapísaných transakcií.
Vlastné stĺpce pridané za tieto stĺpce nástroj nechá tak. Dátum je skutočný dátum vo formáte `DD.MM.YYYY` (dá sa podľa neho
triediť a filtrovať). Odchádzajúce platby majú zápornú sumu. Ukladajú sa len zaúčtované
transakcie (nie čakajúce/blokované).

## 1. Google Sheet

1. V [Google Cloud Console](https://console.cloud.google.com/) vytvorte projekt, zapnite
   **Google Sheets API** a vytvorte **Service account** → Keys → *Add key* → JSON.
2. Tabuľku zdieľajte (Share) s e-mailom service accountu (`...@...iam.gserviceaccount.com`)
   s právom **Editor**.
3. ID tabuľky je časť URL medzi `/d/` a `/edit`.

Hárok (predvolene `Transakcie`) sa vytvorí aj s hlavičkou automaticky.

## 2. Banky

### Fio banka
Internetbanking → Nastavenia → API → Pridať nový token, oprávnenie **len sledovanie účtu**.
Token uložte do premennej prostredia, ktorej názov uvediete v `token_env`.

### Ostatné banky (Enable Banking)
1. Zaregistrujte sa na <https://enablebanking.com/cp/applications> a vytvorte aplikáciu
   (pre vlastné účty stačí bezplatný režim *restricted production*). Uložte si `application_id`
   a stiahnutý súkromný kľúč `.pem`. Ako redirect URL môžete nechať `https://enablebanking.com/`.
2. Doplňte `enablebanking` sekciu do `config.yaml` a potom:

```bash
export ENABLEBANKING_PRIVATE_KEY="$(cat moj-kluc.pem)"
python -m bank_sync eb-banks SK                 # presné názvy bánk
python -m bank_sync eb-auth "Tatra banka" SK    # vypíše odkaz na prihlásenie do banky
# po prihlásení skopírujte z adresy presmerovania hodnotu ?code=...
python -m bank_sync eb-session <code>           # vypíše account_uid jednotlivých účtov
```

3. `account_uid` vložte k účtu do `config.yaml`.

> ⚠️ Súhlas s prístupom (PSD2 consent) platí podľa banky 90–180 dní. Po vypršaní synchronizácia
> daného účtu zlyhá s chybou a treba zopakovať `eb-auth` + `eb-session` (uid účtu sa môže zmeniť).

## 3. Lokálne spustenie

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml    # a upravte

export GOOGLE_APPLICATION_CREDENTIALS=service-account.json
export FIO_TOKEN_1=...

python -m bank_sync sync --dry-run     # len vypíše, čo by zapísal
python -m bank_sync sync               # včerajšok
python -m bank_sync sync --days 7      # posledných 7 dní (už zapísané sa preskočia)
python -m bank_sync sync --from 2026-01-01 --date 2026-06-30   # spätné načítanie
python -m bank_sync sync --account "Fio firemný"               # len jeden účet
```

Každý účet môže mať vlastný hárok (`worksheet:` pri účte); bez neho sa zapisuje do
`google_sheet.worksheet`. Chýbajúci hárok sa vytvorí automaticky.

Ak niektorý účet zlyhá, ostatné sa aj tak zapíšu a príkaz skončí s návratovým kódom 1.

## 4. Denné spúšťanie cez GitHub Actions

Workflow `.github/workflows/daily-sync.yml` beží každý deň o 6:00 slovenského času (v lete aj v zime) a dá sa
spustiť aj ručne (Actions → *Run workflow*, s voliteľným počtom dní).

V repozitári nastavte *Settings → Secrets and variables → Actions*:

| Secret                        | Obsah                                              |
|-------------------------------|----------------------------------------------------|
| `CONFIG_YAML`                 | celý obsah vášho `config.yaml`                     |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | obsah JSON kľúča service accountu                  |
| `ENABLEBANKING_PRIVATE_KEY`   | obsah `.pem` súboru (ak používate Enable Banking)  |
| `FIO_TOKEN_1` …               | token každého Fio účtu – názov podľa `token_env`   |

Pri pridaní ďalšieho Fio účtu doplňte jeho premennú aj do sekcie `env` vo workflow.
GitHub pri zlyhaní behu pošle e-mail – tak sa dozviete napr. o vypršanom súhlase banky.

Alternatívne stačí cron na vlastnom serveri:

```cron
0 7 * * *  cd /opt/bank_sync && .venv/bin/python -m bank_sync sync >> sync.log 2>&1
```

## Testy

```bash
pip install pytest && python -m pytest
```
