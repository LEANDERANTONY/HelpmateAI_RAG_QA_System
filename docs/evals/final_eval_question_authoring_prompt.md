# Final Eval Question Authoring Prompt

Use this prompt with a non-production model family before running HelpmateAI, OpenAI File Search, or Vectara on the held-out documents.

Recommended use:

- author model: Claude, Gemini, or another model family not used as HelpmateAI's production answer model
- input: one full document text at a time
- output: JSON only
- review allowed: remove duplicates, fix malformed JSON, remove questions that are obviously impossible due to extraction failure
- review not allowed: editing questions after seeing retrieval or answer outputs

## Prompt

You are writing a blind evaluation set for a long-document RAG system.

Create questions for the provided document. The questions must be answerable only from the document unless the intent type is `unsupported`.

Return JSON only. Do not include markdown.

Use this schema:

```json
{
  "document_id": "DOCUMENT_ID_HERE",
  "questions": [
    {
      "question_id": "DOCUMENT_ID_HERE-lookup-001",
      "document_id": "DOCUMENT_ID_HERE",
      "question": "Question text",
      "intent_type": "lookup",
      "answerable": true,
      "expected_regions": [
        {
          "section": "section or heading name",
          "page_label": "page label if known"
        }
      ],
      "gold_answer": "Concise reference answer grounded in the document.",
      "gold_answer_notes": "Short note about what evidence supports the answer.",
      "unsupported_reason": ""
    }
  ]
}
```

Allowed intent types:

- `lookup`
- `local_scope`
- `broad_summary`
- `comparison_synthesis`
- `numeric_procedure`
- `unsupported`

Create 30 questions for a full final run, or 10 questions for a pilot.

Target distribution for 30 questions:

- 8 lookup
- 6 local_scope
- 6 broad_summary
- 4 comparison_synthesis
- 3 numeric_procedure
- 3 unsupported

Question rules:

- Use natural user phrasing.
- Avoid copying section headings as the whole question.
- Include questions that require finding the correct section, not just matching a keyword.
- Include at least two broad questions that require synthesizing multiple parts of the document.
- Include at least two local-scope questions that ask about a named chapter, section, article, function, or meeting topic.
- Include numeric questions only when the document actually contains numbers, dates, percentages, costs, quantities, or table values.
- Unsupported questions must sound plausible but must not be answered by the document.
- Do not ask questions that require external knowledge.
- Do not mention page numbers in the question text.
- Do not make the questions easier for any particular retrieval system.

Gold answer rules:

- Keep gold answers concise.
- Cite the supporting section names or page labels in `expected_regions` when available.
- If the answer is distributed across the document, include multiple expected regions.
- For unsupported questions, set `answerable` to false, leave `gold_answer` empty, and explain why in `unsupported_reason`.

Document id:

DOCUMENT_ID_HERE

Document text:

DOCUMENT_TEXT_HERE
