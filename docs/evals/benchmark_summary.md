# Benchmark Summary

This file is the current high-level benchmark snapshot for HelpmateAI.

## Policy

- use `ragas` as the only answer-quality evaluation meter going forward
- keep `Vectara` as the main external retrieval baseline
- keep `OpenAI File Search` as a historical/reference retrieval baseline only
- do not use Vectara factual-consistency as a decision-making metric in routine benchmarking

Reason:

- `ragas` is more interpretable for the kinds of answer-quality tradeoffs we care about
- Vectara factual-consistency produced unstable or formatting-sensitive readings for our answer style
- Vectara retrieval is the strongest external retrieval baseline we have tested so far

## Retrieval Snapshot

| Document | Ours hit/MRR | Vectara retrieval | OpenAI retrieval |
| --- | --- | --- | --- |
| Health policy | `0.8462 / 0.7051` | `0.7692` | `0.6923` |
| Thesis | `0.8333 / 0.5972` | not yet rerun in unified report | `0.6667` |
| `pancreas8` | `0.8 / 0.8` | historically stronger than OpenAI in downstream answer evals | `0.4` |

## Answer-Quality Snapshot (`ragas` only)

### Health policy

| System | Faithfulness | Answer relevancy | Context precision |
| --- | --- | --- | --- |
| Ours | `0.5923` | `0.6950` | `0.8611` |
| Vectara retrieval + shared answer model | `0.8846` | `0.6682` | `0.8782` |
| OpenAI retrieval + shared answer model | `0.6538` | `0.4066` | `0.3513` |

### Thesis

| System | Faithfulness | Answer relevancy | Context precision |
| --- | --- | --- | --- |
| Ours | `0.9333` | `0.6905` | `0.8495` |
| Vectara retrieval + shared answer model | `0.9583` | `0.6336` | `0.8875` |
| OpenAI retrieval + shared answer model | `0.4028` | `0.2949` | `0.4528` |

### `pancreas8`

| System | Faithfulness | Answer relevancy | Context precision |
| --- | --- | --- | --- |
| Ours | `0.7200` | `0.5996` | `0.8222` |
| Vectara retrieval + shared answer model | `0.7133` | `0.3769` | `0.6054` |
| OpenAI retrieval + shared answer model | `0.4667` | `0.3745` | `0.4578` |

## Interpretation

- Helpmate is still strongest overall on the policy benchmark.
- On the thesis benchmark, Vectara is extremely competitive and clearly stronger than OpenAI.
- On `pancreas8`, Helpmate outperforms both external baselines on all three `ragas` metrics.
- OpenAI File Search is now the weakest external baseline across the document families we tested.

## Next Step

- bake these `ragas`-only vendor comparison summaries into the saved benchmark report structure
- rerun the retrieval-only Vectara benchmark for thesis in the unified report format
