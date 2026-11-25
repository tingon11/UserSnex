# Componenti e dati di terze parti

## Definizioni dei siti

- **WhatsMyName** — `lists/whatsmyname.yaml` (scaricato dalla scheda Config, non incluso nel repository) è derivato da
  [wmn-data.json](https://github.com/WebBreacher/WhatsMyName), Copyright (C) 2015-2026 Micah Hoffman,
  licenza [Creative Commons Attribution-ShareAlike 4.0](https://creativecommons.org/licenses/by-sa/4.0/).
  Modifiche: conversione nel formato YAML di UserSnex, categorie tradotte, rimossi i siti per adulti,
  quelli con richiesta POST e i duplicati, rimossi User-Agent e cookie dalle intestazioni. Il file
  derivato resta sotto CC BY-SA 4.0.
- **Sherlock** — `lists/sherlock.yaml` (scaricato, non incluso) è derivato da
  [data.json](https://github.com/sherlock-project/sherlock), Copyright (c) 2019 Sherlock Project,
  licenza MIT (testo sotto). Inclusi solo i siti non già presenti nelle altre liste.

- **Maigret** — `lists/maigret.yaml` (scaricato, non incluso) è derivato da
  [data.json](https://github.com/soxoj/maigret), Copyright (c) 2020-2026 Soxoj, licenza MIT (stesso testo
  della licenza MIT sotto, con questo copyright). Esclusi i siti disattivati, per adulti, con richieste
  POST e i duplicati; i siti meno noti sono nella "lista estesa", attiva solo su richiesta.

## Librerie

CustomTkinter (MIT), Pillow (MIT-CMU), HTTPX (BSD-3-Clause), PyYAML (MIT), colorama (BSD-3-Clause).

---

### Licenza MIT (Sherlock)

Copyright (c) 2019 Sherlock Project

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and
associated documentation files (the "Software"), to deal in the Software without restriction,
including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense,
and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so,
subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial
portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT
LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN
NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
