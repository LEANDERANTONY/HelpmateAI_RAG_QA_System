# Final Eval Held-Out Document Sources

Access date: 2026-04-28

These files are local evaluation assets, not product fixtures. The PDFs are ignored by git under `static/held_out/*.pdf`; rerun `docs/evals/download_final_eval_docs.ps1` to recreate them.

## Selected Documents

| Document id | Local path | Type | Why included | Source | SHA-256 |
| --- | --- | --- | --- | --- | --- |
| `nist-ai-rmf-1-0` | `static/held_out/nist-ai-rmf-1-0.pdf` | policy framework | Structured policy document, good for definitions, functions, obligations, and local-scope questions. | `https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.100-1.pdf` | `7576EDB531D9848825814EE88E28B1795D3A84B435B4B797D3670EAFDC4A89F1` |
| `arxiv-2510-03305` | `static/held_out/arxiv-2510-03305-ml-workflows-climate-modeling.pdf` | research paper | Fresh research-paper style outside the tuned pancreas/report-generation papers. | `https://arxiv.org/pdf/2510.03305` | `24FD975209898B092A0B0BA85FB4DBB3BEBA3978288FC797CA8B6BB018B3FB8C` |
| `upenn-learning-environmental-models-thesis-2022` | `static/held_out/upenn-learning-environmental-models-thesis-2022.pdf` | thesis/dissertation | Thesis-shaped long document about environmental modeling and robotics, structurally similar to user uploads but content-fresh. | `https://core.ac.uk/download/533931293.pdf` | `C542D02E631A5379264AC05C18696EE93A1764EF0FAFEAB72353DA5ED503386D` |
| `fomc-minutes-2026-01-28` | `static/held_out/fomc-minutes-2026-01-28.pdf` | dense public minutes | Short, dense institutional document that tests precision and subtle wording. | `https://www.federalreserve.gov/monetarypolicy/files/fomcminutes20260128.pdf` | `7565DB1BBE4D562B808D50A4150D2B42D00AC87A3ACB1D6D2A9EED5E4974E743` |
| `irena-world-energy-transitions-outlook-2023` | `static/held_out/irena-world-energy-transitions-outlook-2023.pdf` | public technical report | Long, data-heavy report for numeric, table-heavy, and cross-section synthesis questions. | `https://www.irena.org/-/media/Files/IRENA/Agency/Publication/2023/Jun/IRENA_World_energy_transitions_outlook_2023.pdf` | `6C903573C558BF957AAFA69FD17D0E7D0F956ECF9132FE771E9EDB5AF2C91F7E` |

## Notes

- The EU AI Act remains a good candidate, but the official EUR-Lex PDF endpoint returned an empty body from the local script. We should include it later only if we can fetch a stable official PDF or record a trustworthy mirror clearly.
- The MIT thesis candidate was rejected for this first pass because the direct DSpace PDF endpoint returned `403 Forbidden` from the local fetch. The Harvard DASH thesis candidate was also rejected because the repository download endpoint produced byte-varying PDFs across repeated fetches, which made hash verification noisy.
- The current set still covers policy, research paper, thesis, dense minutes, and a long technical report.
- Do not use these documents for threshold tuning before the final held-out eval is frozen.
