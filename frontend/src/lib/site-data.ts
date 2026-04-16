export const proofMetrics = [
  {
    label: "Policy retrieval",
    value: "0.8462",
    note: "Strong retrieval on a real health policy benchmark.",
  },
  {
    label: "Thesis faithfulness",
    value: "0.9333",
    note: "Answers stay well grounded on long academic work.",
  },
  {
    label: "Research precision",
    value: "0.8222",
    note: "Relevant evidence stays focused on a difficult paper.",
  },
  {
    label: "External Baseline",
    value: "Vectara",
    note: "Main managed retrieval baseline used for comparison.",
  },
];

export const capabilityCards = [
  {
    title: "Shows the source",
    copy:
      "Every answer is tied back to the passages it came from, so users can verify what they are reading.",
  },
  {
    title: "Stays honest",
    copy:
      "If the document does not clearly support an answer, Helpmate says so instead of guessing.",
  },
  {
    title: "Handles exact and broad questions",
    copy:
      "It can handle clause lookups, policy wording, thesis chapters, and broader research questions in the same workspace.",
  },
  {
    title: "Built to be checked",
    copy:
      "Benchmarking and answer quality are part of the product, not something hidden in a separate notebook.",
  },
];

export const workflowSteps = [
  {
    step: "Ingest",
    title: "Upload a PDF or Word document",
    body:
      "Bring in a long document once and keep it ready for questioning instead of redoing the same work every time.",
  },
  {
    step: "Structure",
    title: "Understand the document structure",
    body:
      "Sections, headings, and document cues are used to make retrieval more reliable on long, complex files.",
  },
  {
    step: "Retrieve",
    title: "Find the most relevant evidence",
    body:
      "The system pulls the strongest passages for the question instead of relying on a single generic search step.",
  },
  {
    step: "Answer",
    title: "Return an answer with citations",
    body:
      "Users get a clear answer, the passages behind it, and enough context to trust what they are seeing.",
  },
];

export const workspaceHighlights = [
  "Works across policies, theses, and research papers",
  "Clear evidence panels instead of black-box chat replies",
  "Starter questions that adapt to the document type",
  "A calmer workspace built for long-form reading",
];

export const faqItems = [
  {
    question: "Why a custom workspace instead of generic document chat?",
    answer:
      "Helpmate needs calmer long-document reading, clearer evidence presentation, and a stronger API boundary than a generic prototype shell can provide.",
  },
  {
    question: "What makes Helpmate different from generic file chat?",
    answer:
      "Helpmate is tuned for long-document QA with explicit retrieval routing, citations, and benchmark-driven quality checks.",
  },
  {
    question: "Is Vectara still part of the product story?",
    answer:
      "Yes. Vectara remains the main external retrieval baseline we use to prove where the local stack stands.",
  },
  {
    question: "How are answers evaluated now?",
    answer:
      "Ragas is the main answer-quality meter, while retrieval comparisons stay visible as part of the credibility layer.",
  },
];
