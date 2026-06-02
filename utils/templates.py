"""
Template registry for the page copy production app.

Each template is a dict with:
  - name: display name shown in the UI
  - page_type: "blog" | "case_study" | "glossary"
  - description: shown in the UI to help users pick
  - sections: ordered list of section definitions

Each section definition:
  - name: internal name, used for keyword assignment and section mapping
  - label: H2 heading placeholder (AI can rename within rules)
  - purpose: one sentence, injected into the section prompt
  - word_count: [min, max]
  - keyword_slot: "primary" | "supporting" | "lsi" | "none"
  - heading_level: "h1" | "h2" | "h3" | "none" (no heading, e.g. intro body)
  - prompt_rules: section-specific instructions injected into the AI prompt
"""

TEMPLATES = {

    # ── BLOG: Standard Informational ─────────────────────────────────────────
    "blog_standard": {
        "name": "Standard Informational",
        "page_type": "blog",
        "description": "Best for: educational content, guides, and how things work. The most versatile blog format.",
        "sections": [
            {
                "name": "intro",
                "label": "Introduction",
                "purpose": "State the problem or question the reader has. Promise what this page answers. Hook without fluff.",
                "word_count": [120, 180],
                "keyword_slot": "primary",
                "heading_level": "none",
                "prompt_rules": (
                    "Open with the reader's problem or question directly. "
                    "Include the primary keyword naturally within the first two sentences. "
                    "End the intro with a clear statement of what the reader will learn. "
                    "No generic openings like 'In today's world' or 'Are you wondering'. "
                    "No em dashes."
                ),
            },
            {
                "name": "context",
                "label": "Why This Matters",
                "purpose": "Establish the stakes and why this topic is relevant now. Sets up the body sections.",
                "word_count": [150, 220],
                "keyword_slot": "supporting",
                "heading_level": "h2",
                "prompt_rules": (
                    "Explain why this topic matters to the reader's situation. "
                    "Use data, trends, or a concrete scenario to ground it. "
                    "Do not repeat the intro. Move the argument forward. "
                    "H2 must be a question or a statement readers would recognise as their own concern."
                ),
            },
            {
                "name": "body_1",
                "label": "Core Section 1",
                "purpose": "Answer the first major sub-question the reader has about this topic.",
                "word_count": [200, 300],
                "keyword_slot": "supporting",
                "heading_level": "h2",
                "prompt_rules": (
                    "H2 must reflect a real user question or search phrase. "
                    "Include the supporting keyword in the H2 or first paragraph naturally. "
                    "Use concrete examples, steps, or comparisons. Avoid vague generalisations. "
                    "No em dashes. No bullet point lists unless the content is genuinely list-like."
                ),
            },
            {
                "name": "body_2",
                "label": "Core Section 2",
                "purpose": "Answer the second major sub-question. Build on body_1 without repeating it.",
                "word_count": [200, 300],
                "keyword_slot": "supporting",
                "heading_level": "h2",
                "prompt_rules": (
                    "This section should deepen the reader's understanding, not repeat body_1. "
                    "H2 should be distinct from the previous section heading. "
                    "Use a different angle: if body_1 covered what, this covers how or why. "
                    "No em dashes."
                ),
            },
            {
                "name": "body_3",
                "label": "Core Section 3",
                "purpose": "Address a third angle, common mistake, or practical consideration.",
                "word_count": [200, 300],
                "keyword_slot": "lsi",
                "heading_level": "h2",
                "prompt_rules": (
                    "Cover a practical consideration, common mistake, or advanced angle. "
                    "This section should feel useful to someone who already read the first two body sections. "
                    "No em dashes. Can use a short numbered list if genuinely appropriate."
                ),
            },
            {
                "name": "summary",
                "label": "Key Takeaways",
                "purpose": "Summarise the core points in scannable format. Re-hits primary keyword naturally.",
                "word_count": [80, 130],
                "keyword_slot": "primary",
                "heading_level": "h2",
                "prompt_rules": (
                    "Write 3 to 5 short bullet points summarising the most actionable insights from the page. "
                    "Each bullet should be a complete sentence. No vague bullets. "
                    "Re-include the primary keyword naturally in the H2 or first bullet. "
                    "Do not introduce new information here."
                ),
            },
            {
                "name": "faq",
                "label": "Frequently Asked Questions",
                "purpose": "Answer 3 to 5 real questions readers have at this stage. Pulls from PAA data.",
                "word_count": [200, 320],
                "keyword_slot": "lsi",
                "heading_level": "h2",
                "prompt_rules": (
                    "Write 3 to 5 FAQ items using the PAA questions provided as source. "
                    "Each answer must be 2 to 4 sentences. Direct and specific. "
                    "Do not repeat information already covered in the body. "
                    "Format as: Question on its own line, then answer paragraph."
                ),
            },
            {
                "name": "cta",
                "label": "Next Steps",
                "purpose": "Soft conversion. Business-type-aware. No hard-sell language.",
                "word_count": [60, 100],
                "keyword_slot": "none",
                "heading_level": "h2",
                "prompt_rules": (
                    "Write a short closing paragraph that leads the reader to a natural next action. "
                    "B2B: contact, request a demo, or download. "
                    "Ecommerce: shop, browse, or explore. "
                    "Service/local: call, get a quote, book. "
                    "No branded consumer CTAs on B2B pages. "
                    "No exclamation marks. No em dashes."
                ),
            },
        ],
    },

    # ── BLOG: Listicle ───────────────────────────────────────────────────────
    "blog_listicle": {
        "name": "Listicle",
        "page_type": "blog",
        "description": "Best for: top X lists, roundups, collections. High CTR format for informational intent.",
        "sections": [
            {
                "name": "intro",
                "label": "Introduction",
                "purpose": "Frame what the list covers and why these items were chosen.",
                "word_count": [100, 160],
                "keyword_slot": "primary",
                "heading_level": "none",
                "prompt_rules": (
                    "State what the list covers and the selection criteria briefly. "
                    "Include primary keyword in first two sentences. "
                    "No padding. Get to the list quickly."
                ),
            },
            {
                "name": "list_items",
                "label": "The List",
                "purpose": "The numbered items. Each item gets an H3, a 2 to 4 sentence description, and one concrete example.",
                "word_count": [600, 1000],
                "keyword_slot": "supporting",
                "heading_level": "h2",
                "prompt_rules": (
                    "Write 5 to 10 numbered list items. "
                    "Each item: H3 with the item name, then 2 to 4 sentences of explanation. "
                    "Every item must include at least one concrete, specific detail. No vague generalisations. "
                    "Distribute supporting and LSI keywords naturally across the items. "
                    "No em dashes."
                ),
            },
            {
                "name": "faq",
                "label": "Frequently Asked Questions",
                "purpose": "Answer 3 PAA questions related to the list topic.",
                "word_count": [150, 250],
                "keyword_slot": "lsi",
                "heading_level": "h2",
                "prompt_rules": (
                    "Write 3 FAQ items using PAA questions provided. "
                    "Keep answers to 2 to 3 sentences each. Direct and specific."
                ),
            },
            {
                "name": "cta",
                "label": "Next Steps",
                "purpose": "Soft conversion close.",
                "word_count": [60, 100],
                "keyword_slot": "none",
                "heading_level": "h2",
                "prompt_rules": (
                    "Short closing paragraph with a natural next action. Business-type-aware. No em dashes."
                ),
            },
        ],
    },

    # ── BLOG: How-to / Guide ─────────────────────────────────────────────────
    "blog_howto": {
        "name": "How-to / Guide",
        "page_type": "blog",
        "description": "Best for: step-by-step instructions, tutorials, and process walkthroughs.",
        "sections": [
            {
                "name": "intro",
                "label": "Introduction",
                "purpose": "State the end goal the reader is trying to achieve and what this guide covers.",
                "word_count": [100, 160],
                "keyword_slot": "primary",
                "heading_level": "none",
                "prompt_rules": (
                    "Open with the outcome the reader wants. "
                    "Include primary keyword in first two sentences. "
                    "End with a brief mention of what the steps cover."
                ),
            },
            {
                "name": "prerequisites",
                "label": "What You Need Before You Start",
                "purpose": "Set expectations: tools, knowledge, or conditions needed.",
                "word_count": [80, 130],
                "keyword_slot": "none",
                "heading_level": "h2",
                "prompt_rules": (
                    "Short list of prerequisites or things the reader should have ready. "
                    "Be specific. Avoid vague items like 'a computer' unless context demands it."
                ),
            },
            {
                "name": "steps",
                "label": "Step-by-Step",
                "purpose": "The numbered steps. Each step is an H3 with clear instruction and rationale.",
                "word_count": [600, 900],
                "keyword_slot": "supporting",
                "heading_level": "h2",
                "prompt_rules": (
                    "Write 4 to 8 numbered steps. Each step: H3 with imperative verb phrase (e.g. 'Set Up Your Account'), "
                    "then 3 to 5 sentences covering what to do, how to do it, and why it matters. "
                    "Include supporting keywords naturally across the steps. "
                    "Call out common mistakes where relevant. No em dashes."
                ),
            },
            {
                "name": "tips",
                "label": "Tips and Common Mistakes",
                "purpose": "Practical tips that save the reader time or prevent failure.",
                "word_count": [150, 220],
                "keyword_slot": "lsi",
                "heading_level": "h2",
                "prompt_rules": (
                    "Write 3 to 5 tips or common mistakes to avoid. "
                    "Be specific. Each tip should be actionable, not generic advice."
                ),
            },
            {
                "name": "faq",
                "label": "Frequently Asked Questions",
                "purpose": "Answer PAA questions relevant to this how-to topic.",
                "word_count": [150, 250],
                "keyword_slot": "lsi",
                "heading_level": "h2",
                "prompt_rules": "Write 3 FAQ items from PAA questions. 2 to 3 sentences per answer.",
            },
            {
                "name": "cta",
                "label": "Next Steps",
                "purpose": "Soft conversion close.",
                "word_count": [60, 100],
                "keyword_slot": "none",
                "heading_level": "h2",
                "prompt_rules": "Short closing with natural next action. Business-type-aware. No em dashes.",
            },
        ],
    },

    # ── BLOG: Comparison ─────────────────────────────────────────────────────
    "blog_comparison": {
        "name": "Comparison / vs Post",
        "page_type": "blog",
        "description": "Best for: X vs Y, alternatives, or option-comparison content. High commercial intent.",
        "sections": [
            {
                "name": "intro",
                "label": "Introduction",
                "purpose": "Frame the comparison decision the reader is facing.",
                "word_count": [100, 160],
                "keyword_slot": "primary",
                "heading_level": "none",
                "prompt_rules": (
                    "Open by naming the decision the reader is trying to make. "
                    "Include primary keyword in first two sentences. "
                    "No 'in this article we will'. Get to the point."
                ),
            },
            {
                "name": "overview",
                "label": "Quick Overview",
                "purpose": "Brief summary of each option being compared. No verdict yet.",
                "word_count": [150, 220],
                "keyword_slot": "supporting",
                "heading_level": "h2",
                "prompt_rules": (
                    "One short paragraph per option being compared. "
                    "Describe what each one is, who it is for, and its main strength. "
                    "Do not give a verdict here. Save that for the end."
                ),
            },
            {
                "name": "criteria_1",
                "label": "Comparison Criteria 1",
                "purpose": "Compare both options on the first key dimension.",
                "word_count": [180, 260],
                "keyword_slot": "supporting",
                "heading_level": "h2",
                "prompt_rules": (
                    "H2 names the dimension being compared (e.g. 'Pricing', 'Ease of Use', 'Scalability'). "
                    "Be specific. Use concrete numbers, features, or scenarios. "
                    "Do not pad with vague statements like 'both have their pros and cons'."
                ),
            },
            {
                "name": "criteria_2",
                "label": "Comparison Criteria 2",
                "purpose": "Compare both options on the second key dimension.",
                "word_count": [180, 260],
                "keyword_slot": "supporting",
                "heading_level": "h2",
                "prompt_rules": "Same rules as criteria_1. Different dimension.",
            },
            {
                "name": "criteria_3",
                "label": "Comparison Criteria 3",
                "purpose": "Compare both options on the third key dimension.",
                "word_count": [180, 260],
                "keyword_slot": "lsi",
                "heading_level": "h2",
                "prompt_rules": "Same rules as criteria_1. Third distinct dimension.",
            },
            {
                "name": "verdict",
                "label": "Which Should You Choose?",
                "purpose": "Give a clear recommendation with conditions. No fence-sitting.",
                "word_count": [150, 200],
                "keyword_slot": "primary",
                "heading_level": "h2",
                "prompt_rules": (
                    "Give a direct recommendation. State which option is better and for whom. "
                    "Use conditional framing: 'If you need X, go with A. If you need Y, go with B.' "
                    "No vague both-sides conclusions. Readers came here to make a decision."
                ),
            },
            {
                "name": "faq",
                "label": "Frequently Asked Questions",
                "purpose": "Answer 3 PAA questions about the comparison topic.",
                "word_count": [150, 250],
                "keyword_slot": "lsi",
                "heading_level": "h2",
                "prompt_rules": "3 FAQ items from PAA questions. 2 to 3 sentences per answer.",
            },
            {
                "name": "cta",
                "label": "Next Steps",
                "purpose": "Soft conversion close.",
                "word_count": [60, 100],
                "keyword_slot": "none",
                "heading_level": "h2",
                "prompt_rules": "Short closing with natural next action. Business-type-aware. No em dashes.",
            },
        ],
    },

    # ── CASE STUDY ───────────────────────────────────────────────────────────
    "case_study_b2b": {
        "name": "B2B Case Study",
        "page_type": "case_study",
        "description": "Research-backed B2B case study structure. Situation, Trigger, Barrier, Solution, Results flow.",
        "sections": [
            {
                "name": "headline_snapshot",
                "label": "Headline and Client Snapshot",
                "purpose": "Result-first headline. Client snapshot box: industry, size, challenge tag, KPI achieved.",
                "word_count": [60, 100],
                "keyword_slot": "primary",
                "heading_level": "h1",
                "prompt_rules": (
                    "Write a headline that leads with the single most impressive measurable result. "
                    "Then write a 3 to 4 line snapshot: Client industry, company size, main challenge, key result. "
                    "Primary keyword must appear in the headline naturally. "
                    "No vague headlines like 'How We Helped Company X'. Lead with the number or outcome."
                ),
            },
            {
                "name": "situation",
                "label": "The Situation",
                "purpose": "What the client's world looked like before. Sets context. Industry keywords go here.",
                "word_count": [150, 220],
                "keyword_slot": "supporting",
                "heading_level": "h2",
                "prompt_rules": (
                    "Describe the client's situation before the engagement. "
                    "Name the industry context, the scale of their operations, and the environment they were operating in. "
                    "Pull in industry-specific keywords naturally. "
                    "Do not introduce the problem here. That comes next. This is purely context."
                ),
            },
            {
                "name": "trigger",
                "label": "What Changed",
                "purpose": "The trigger event that forced the client to act. Business and emotional logic.",
                "word_count": [100, 160],
                "keyword_slot": "supporting",
                "heading_level": "h2",
                "prompt_rules": (
                    "Name the specific event, pressure, or change that made the status quo unsustainable. "
                    "This is the 'why now' moment. It should feel real and specific. "
                    "Both business logic and human stakes should be visible."
                ),
            },
            {
                "name": "barrier",
                "label": "The Challenge",
                "purpose": "What made solving this hard. The section buyers identify with most.",
                "word_count": [120, 180],
                "keyword_slot": "lsi",
                "heading_level": "h2",
                "prompt_rules": (
                    "Describe what made the problem difficult to solve. "
                    "This is the section readers will most identify with. It should mirror their own frustrations. "
                    "Be specific about the obstacles: technical, organisational, budget, or market. "
                    "Do not mention the solution here."
                ),
            },
            {
                "name": "solution",
                "label": "The Solution",
                "purpose": "What was done and how. Product and service names go here naturally.",
                "word_count": [200, 300],
                "keyword_slot": "supporting",
                "heading_level": "h2",
                "prompt_rules": (
                    "Describe the solution in concrete terms. What was done, in what order, and why those choices were made. "
                    "Name specific products, services, or methodologies where provided. "
                    "Include implementation timeline signals (time-to-value is the most trusted B2B KPI). "
                    "No vague language like 'a comprehensive approach'. Be specific."
                ),
            },
            {
                "name": "results",
                "label": "The Results",
                "purpose": "Metrics first, then narrative. Before/after framing.",
                "word_count": [150, 220],
                "keyword_slot": "primary",
                "heading_level": "h2",
                "prompt_rules": (
                    "Lead with the most impressive metric. Then add 2 to 3 supporting metrics. "
                    "Use before/after framing where data allows. "
                    "Include time-to-value: how long until results were visible. "
                    "If placeholder metrics are provided in the brief, use them exactly. "
                    "If not, write placeholders in brackets like [X% improvement in Y]. "
                    "Do not pad with vague outcome statements."
                ),
            },
            {
                "name": "quote",
                "label": "Client Quote",
                "purpose": "Single attributed quote that names the result or removes a key objection.",
                "word_count": [30, 60],
                "keyword_slot": "none",
                "heading_level": "none",
                "prompt_rules": (
                    "If a quote is provided in the brief, use it exactly and add attribution. "
                    "If not, write a realistic placeholder in quotation marks with [Name, Title, Company] attribution. "
                    "The quote should name a result or a before/after contrast. "
                    "No generic praise quotes like 'They were great to work with'."
                ),
            },
            {
                "name": "faq",
                "label": "Frequently Asked Questions",
                "purpose": "2 to 3 questions buyers have at this stage of the funnel.",
                "word_count": [120, 200],
                "keyword_slot": "lsi",
                "heading_level": "h2",
                "prompt_rules": (
                    "Write 2 to 3 FAQ items using PAA questions or late-funnel buyer questions. "
                    "Answers should be 2 to 3 sentences. Address objections or implementation concerns."
                ),
            },
            {
                "name": "cta",
                "label": "Work With Us",
                "purpose": "Stage-aware CTA: demo, contact, or related case studies.",
                "word_count": [60, 100],
                "keyword_slot": "none",
                "heading_level": "h2",
                "prompt_rules": (
                    "Write a short CTA paragraph. "
                    "For B2B: request a demo, get in touch, or see related case studies. "
                    "No exclamation marks. No em dashes. No 'unleash your potential' language."
                ),
            },
        ],
    },

    # ── GLOSSARY ─────────────────────────────────────────────────────────────
    "glossary": {
        "name": "Glossary / Definition Page",
        "page_type": "glossary",
        "description": "Best for: defining industry terms, building topical authority, capturing definition-intent queries.",
        "sections": [
            {
                "name": "definition",
                "label": "Definition",
                "purpose": "Clear, direct definition of the term. Primary keyword in first sentence.",
                "word_count": [80, 130],
                "keyword_slot": "primary",
                "heading_level": "none",
                "prompt_rules": (
                    "Start with '[Term] is...' or '[Term] refers to...'. "
                    "Include the primary keyword in the first sentence. "
                    "Be precise. No vague openers."
                ),
            },
            {
                "name": "expanded",
                "label": "In More Detail",
                "purpose": "Expand on the definition with context, nuance, or how it is used in practice.",
                "word_count": [200, 300],
                "keyword_slot": "supporting",
                "heading_level": "h2",
                "prompt_rules": (
                    "Go deeper than the definition. Explain how the term is used in context, "
                    "common variations, or how it differs from related terms. "
                    "Include supporting keyword naturally. No em dashes."
                ),
            },
            {
                "name": "examples",
                "label": "Examples",
                "purpose": "2 to 3 concrete real-world examples of the term in use.",
                "word_count": [150, 250],
                "keyword_slot": "lsi",
                "heading_level": "h2",
                "prompt_rules": (
                    "Write 2 to 3 specific examples. Each example should show the term applied in a real scenario. "
                    "Name industries, tools, or situations where relevant. No hypothetical filler."
                ),
            },
            {
                "name": "related_terms",
                "label": "Related Terms",
                "purpose": "Brief definitions of 3 to 5 related terms. Internal link signals.",
                "word_count": [150, 220],
                "keyword_slot": "lsi",
                "heading_level": "h2",
                "prompt_rules": (
                    "List 3 to 5 related terms with a 1 to 2 sentence definition each. "
                    "Choose terms that are genuinely related, not just adjacent. "
                    "Format as: Term name then definition, one per paragraph."
                ),
            },
            {
                "name": "faq",
                "label": "Frequently Asked Questions",
                "purpose": "3 PAA questions about this term.",
                "word_count": [150, 250],
                "keyword_slot": "lsi",
                "heading_level": "h2",
                "prompt_rules": "3 FAQ items from PAA questions. 2 to 3 sentences per answer.",
            },
            {
                "name": "cta",
                "label": "Learn More",
                "purpose": "Soft CTA pointing to related content or a conversion action.",
                "word_count": [50, 80],
                "keyword_slot": "none",
                "heading_level": "h2",
                "prompt_rules": "Short closing. Business-type-aware. No em dashes.",
            },
        ],
    },

    # ── HOMEPAGE ──────────────────────────────────────────────────────────────
    "homepage": {
        "name": "Homepage",
        "page_type": "homepage",
        "description": "Full homepage copy. Clear positioning, core services overview, trust signals, differentiators, and CTA. The H1 passes the five-second test: what you do, who for, and the result.",
        "sections": [
            {
                "name": "hero",
                "label": "Hero",
                "purpose": "State immediately what the business does, who it serves, and what result they can expect. Primary keyword in H1. Single primary CTA.",
                "word_count": [80, 140],
                "keyword_slot": "primary",
                "heading_level": "h1",
                "prompt_rules": (
                    "Write the hero section: H1 headline and a supporting subheadline only. "
                    "The H1 must answer three questions in one sentence: what you do, who you do it for, and the result or outcome. "
                    "No vague mission statements, no clever wordplay. Example structure: '[Service] for [audience] who want [outcome].' "
                    "Include the primary keyword naturally in the H1. "
                    "Subheadline (1 to 2 sentences) expands on the H1 with the most compelling proof point or differentiator. "
                    "Do not write the CTA button text. Do not mention internal navigation. "
                    "No em dashes. No exclamation marks."
                ),
            },
            {
                "name": "trust_bar",
                "label": "Social Proof Bar",
                "purpose": "One short trust signal that immediately follows the hero. Shifts reader mindset from 'is this legit?' to 'this feels solid'.",
                "word_count": [40, 80],
                "keyword_slot": "none",
                "heading_level": "none",
                "prompt_rules": (
                    "Write a single short social proof statement — one sentence or a short list of three trust signals. "
                    "Choose from: a specific number of clients or projects, a named outcome, a meaningful quote from a client (use placeholder if none provided), or a 'trusted by' statement with industry or audience type. "
                    "Examples: 'Trusted by 200+ B2B manufacturers across the US and EU.' or 'Rated 4.9 stars across 180 Google reviews.' "
                    "Be specific. Never write vague statements like 'quality you can trust'. "
                    "No heading needed. No em dashes."
                ),
            },
            {
                "name": "services_overview",
                "label": "What We Do",
                "purpose": "Introduce core services briefly so visitors self-identify and move to the right place. Not a full services list. Opens the right doors.",
                "word_count": [120, 200],
                "keyword_slot": "supporting",
                "heading_level": "h2",
                "prompt_rules": (
                    "Write an H2 and 2 to 4 short service blocks. Each block: one sentence naming the service and one sentence explaining the outcome it delivers. "
                    "Focus on what the visitor gets, not what the business does. "
                    "H2 should be outcome-oriented, not a generic 'Our Services' heading. "
                    "Do not list pricing, do not go into process detail. "
                    "If the business has one core service, describe it from 2 to 3 angles (e.g., industries served, output types). "
                    "No em dashes. No exclamation marks."
                ),
            },
            {
                "name": "differentiators",
                "label": "Why Choose Us",
                "purpose": "Three specific differentiators that separate this business from the obvious alternatives. Benefit-focused, not feature-focused.",
                "word_count": [150, 250],
                "keyword_slot": "supporting",
                "heading_level": "h2",
                "prompt_rules": (
                    "Write an H2 and exactly 3 differentiator blocks. Each block: a bold label (3 to 5 words), then 2 to 3 sentences of explanation. "
                    "Each differentiator must be specific and provable — not 'quality service' or 'experienced team'. "
                    "Good differentiators reference: a specific process, a time-to-value promise, a guarantee, a specialisation that competitors lack, or a result metric. "
                    "Benefit-focused copy converts significantly better than feature-focused copy. Lead with what the client gains, not what the business has. "
                    "No em dashes. No exclamation marks."
                ),
            },
            {
                "name": "social_proof",
                "label": "Client Results",
                "purpose": "One or two specific social proof items. A testimonial or a result stat. Makes the differentiators credible.",
                "word_count": [80, 150],
                "keyword_slot": "none",
                "heading_level": "h2",
                "prompt_rules": (
                    "Write one short testimonial block and/or one result stat. "
                    "If a client quote is provided in the brief, use it exactly with attribution. "
                    "If not, write a realistic placeholder in quotation marks with [Name, Title, Company] attribution. "
                    "The quote or stat should name a specific result or before/after contrast, not generic praise. "
                    "Example: 'We reduced onboarding time from 6 weeks to 8 days.' "
                    "No vague statements like 'they were amazing to work with'. "
                    "No em dashes."
                ),
            },
            {
                "name": "cta_close",
                "label": "Get Started",
                "purpose": "Closing CTA section. Clear and low-friction. Guides the reader to the next step without pressure.",
                "word_count": [60, 100],
                "keyword_slot": "none",
                "heading_level": "h2",
                "prompt_rules": (
                    "Write a short closing section: an H2 that names the next step, then 1 to 2 sentences that make reaching out feel easy and natural. "
                    "B2B: 'Request a consultation', 'Get a quote', 'Talk to the team'. "
                    "Service/local: 'Book a call', 'Get in touch'. "
                    "Ecommerce: 'Browse the range', 'Shop [category]'. "
                    "Do not use high-pressure language. Do not use exclamation marks. "
                    "The tone should feel like an invitation, not a close. No em dashes."
                ),
            },
        ],
    },

    # ── SERVICE PAGE ──────────────────────────────────────────────────────────
    "service_page": {
        "name": "Service Page",
        "page_type": "service",
        "description": "High-converting service page for a specific offering. Follows the proven framework: clarity, benefits, pain points, solution, social proof, process, FAQs, CTA. User-focused over keyword-focused.",
        "sections": [
            {
                "name": "hero",
                "label": "Service Hero",
                "purpose": "Immediately communicate what the service is, who it is for, and the outcome. Keyword in H1. Primary CTA.",
                "word_count": [80, 150],
                "keyword_slot": "primary",
                "heading_level": "h1",
                "prompt_rules": (
                    "Write the hero section: H1 headline and a subheadline (1 to 2 sentences). "
                    "H1 must be clear above all else. Include the primary keyword. "
                    "Structure: '[Service] for [audience type] in [location if local]' or '[What you get] from [brand]'. "
                    "Subheadline expands on who it is for and the primary outcome they can expect. "
                    "No clever wordplay. No vague mission statements. "
                    "No em dashes. No exclamation marks."
                ),
            },
            {
                "name": "benefits",
                "label": "Key Benefits",
                "purpose": "Three key benefits that increase desire for the service by showing what the client walks away with. Benefit-focused converts 20 to 40 percent better than feature-focused.",
                "word_count": [150, 250],
                "keyword_slot": "supporting",
                "heading_level": "h2",
                "prompt_rules": (
                    "Write exactly 3 benefit blocks. Each block: a short bold label (3 to 5 words), then 2 to 3 sentences. "
                    "Every benefit must describe an outcome the client achieves, not a feature of the service. "
                    "Instead of 'Experienced team', write 'Faster time to result' or 'Less back-and-forth'. "
                    "Include the supporting keyword naturally in one of the benefit explanations. "
                    "B2B: focus on efficiency, ROI, risk reduction. Service/local: focus on outcome, speed, reliability. "
                    "No em dashes."
                ),
            },
            {
                "name": "pain_points",
                "label": "The Problem",
                "purpose": "Show you understand the reader's frustration before presenting the solution. The section readers identify with most.",
                "word_count": [120, 200],
                "keyword_slot": "none",
                "heading_level": "h2",
                "prompt_rules": (
                    "Write a section that names the core problem or frustration the target client is experiencing. "
                    "H2 should be a pain point statement that readers would nod at, not a generic 'Challenges' heading. "
                    "Describe the situation: what the client is trying to do, what is getting in the way, and what it is costing them. "
                    "Be specific about the obstacles — technical, time, cost, or risk. "
                    "Do not mention the solution here. This section is purely about the problem. "
                    "The reader should feel understood and think: they get it. "
                    "No em dashes. No exclamation marks."
                ),
            },
            {
                "name": "solution",
                "label": "How We Help",
                "purpose": "Present the service as the direct solution to the pain points above. Specific about what is done, for whom, and how.",
                "word_count": [180, 280],
                "keyword_slot": "supporting",
                "heading_level": "h2",
                "prompt_rules": (
                    "Write the solution section: H2 followed by 2 to 3 paragraphs. "
                    "Describe what the service does, who it is for, and the advantages it delivers. "
                    "Name specific deliverables, tools, methods, or specialisations where provided in the brief. "
                    "Focus on what the client gets, not on what the business does. "
                    "Incorporate supporting keyword naturally. "
                    "Language should shift from the problem-focused tone of the previous section to a clear, confident solution tone. "
                    "No em dashes. No exclamation marks."
                ),
            },
            {
                "name": "social_proof",
                "label": "Client Results",
                "purpose": "Social proof immediately after the solution to validate the claims. Testimonials and/or case study excerpts.",
                "word_count": [100, 180],
                "keyword_slot": "none",
                "heading_level": "h2",
                "prompt_rules": (
                    "Write 1 to 2 social proof items: a testimonial, a result metric, or a short case study excerpt. "
                    "If a quote is provided in the brief, use it exactly. "
                    "If not, write a realistic placeholder: a quoted statement in quotation marks with [Name, Title, Company] and a before/after result reference. "
                    "Quote should name a specific result, not generic praise. "
                    "Slot this near the Why Choose Us content to lend credibility to the differentiators. "
                    "Avoid manufactured superlatives. No em dashes."
                ),
            },
            {
                "name": "process",
                "label": "How It Works",
                "purpose": "Walk the reader through what happens when they engage. Sets expectations, reduces hesitation, builds trust.",
                "word_count": [150, 250],
                "keyword_slot": "none",
                "heading_level": "h2",
                "prompt_rules": (
                    "Write 3 to 5 numbered process steps. Each step: a bold label, then 2 to 3 sentences. "
                    "Show: what happens first (how to get in touch or get started), a summary of what the business does, and the outcome the client receives. "
                    "Do not list every internal milestone. Only show enough to help the reader picture themselves in the process. "
                    "Keep the steps incredibly simple: start, what happens, end result. "
                    "This section is not about selling. It is about reducing uncertainty. "
                    "No em dashes. No exclamation marks."
                ),
            },
            {
                "name": "faq",
                "label": "Frequently Asked Questions",
                "purpose": "Anticipate and address objections and lingering questions. Keyword-enriched FAQ section.",
                "word_count": [200, 320],
                "keyword_slot": "lsi",
                "heading_level": "h2",
                "prompt_rules": (
                    "Write 4 to 6 FAQ items using PAA questions provided and common service-page objections. "
                    "Each answer: 2 to 4 direct sentences. No padding. "
                    "Address objections like: cost, timeframe, suitability, what makes you different, and how to get started. "
                    "Include LSI keywords naturally across answers. "
                    "Format: Question as H3 or bold line, then answer paragraph. "
                    "No em dashes."
                ),
            },
            {
                "name": "cta",
                "label": "Get in Touch",
                "purpose": "Final conversion section. Clear and frictionless. Business-type-aware CTA.",
                "word_count": [60, 100],
                "keyword_slot": "none",
                "heading_level": "h2",
                "prompt_rules": (
                    "Write a short closing CTA section: H2 naming the next action, then 1 to 2 sentences. "
                    "B2B: request a demo, get a quote, schedule a call. "
                    "Service/local: call, book, get a free estimate. "
                    "Make the next step feel easy and low-commitment. "
                    "No em dashes. No exclamation marks. No high-pressure language."
                ),
            },
        ],
    },

    # ── LOCAL SERVICE PAGE ────────────────────────────────────────────────────
    "local_service_page": {
        "name": "Local Service Page",
        "page_type": "local",
        "description": "Location-specific service page designed to rank in the Google Maps pack and local organic results. Service + location in H1. References local context throughout. LocalBusiness schema-ready.",
        "sections": [
            {
                "name": "hero",
                "label": "Local Hero",
                "purpose": "Service and location in H1. Immediate clarity on what is offered, where, and to whom.",
                "word_count": [80, 150],
                "keyword_slot": "primary",
                "heading_level": "h1",
                "prompt_rules": (
                    "Write the hero: H1 headline and a subheadline (1 to 2 sentences). "
                    "H1 must include the service name and the location naturally. Example: 'Commercial Roofing Services in Denver, CO'. "
                    "It should be clear and direct, not clever. Include the primary keyword. "
                    "Subheadline names the service area and the primary outcome for clients in that location. "
                    "If a specific city, suburb, or region is provided in the brief, use it. "
                    "No em dashes. No exclamation marks."
                ),
            },
            {
                "name": "local_intro",
                "label": "Serving [Location]",
                "purpose": "Establish local presence and relevance. References the area, who is served, and what the service covers in this specific market.",
                "word_count": [150, 240],
                "keyword_slot": "primary",
                "heading_level": "none",
                "prompt_rules": (
                    "Write 2 paragraphs that establish genuine local relevance. "
                    "Paragraph 1: describe the service and who it serves in this location. Include the primary keyword in the first two sentences. "
                    "Paragraph 2: reference the local context — the area's industries, common local needs, or service-area coverage (cities, suburbs, neighborhoods served). "
                    "This is not generic service description. It is location-specific. "
                    "Do not use city name padding (e.g. 'Denver businesses in Denver'). Reference the location as a reader from that area would expect. "
                    "No em dashes."
                ),
            },
            {
                "name": "services_in_location",
                "label": "Our Services in [Location]",
                "purpose": "List the specific services offered in this location. Keyword-enriched service descriptions.",
                "word_count": [200, 300],
                "keyword_slot": "supporting",
                "heading_level": "h2",
                "prompt_rules": (
                    "Write 3 to 5 service blocks for this location. Each block: a bold service name, then 2 to 3 sentences describing that service in the context of the local market. "
                    "Reference the location naturally within at least 2 of the service descriptions. "
                    "Include the supporting keyword naturally. "
                    "Focus on outcomes and local applicability, not generic service descriptions. "
                    "No em dashes."
                ),
            },
            {
                "name": "why_local",
                "label": "Why Choose Us in [Location]",
                "purpose": "Differentiators that are specifically relevant to serving this location. Local knowledge, coverage, response time, or area-specific experience.",
                "word_count": [150, 240],
                "keyword_slot": "supporting",
                "heading_level": "h2",
                "prompt_rules": (
                    "Write 3 differentiator blocks that are specific to serving this location. "
                    "Examples: local team, same-day coverage, years of local experience, knowledge of local regulations, or area-specific case studies. "
                    "Each block: bold label (3 to 5 words), then 2 to 3 sentences. "
                    "Avoid generic differentiators like 'quality service' or 'experienced team'. "
                    "Reference the location in at least one block. "
                    "No em dashes."
                ),
            },
            {
                "name": "service_area",
                "label": "Areas We Serve",
                "purpose": "Define the service area coverage. Signals to Google which geographic queries this page is relevant for.",
                "word_count": [100, 180],
                "keyword_slot": "lsi",
                "heading_level": "h2",
                "prompt_rules": (
                    "Write a short paragraph describing the service area coverage. "
                    "Name the primary city/region and surrounding areas, suburbs, or towns served. "
                    "If specific coverage areas are provided in the brief, list them. "
                    "If not, write a realistic placeholder: 'We serve [Primary City] and the surrounding areas including [suburb 1], [suburb 2], and [suburb 3]. Contact us to confirm coverage in your area.' "
                    "Keep the tone helpful, not promotional. This section is informational. "
                    "Include LSI keywords naturally. No em dashes."
                ),
            },
            {
                "name": "local_social_proof",
                "label": "What Local Clients Say",
                "purpose": "Testimonials or results from clients in or near this location. Local social proof carries more weight for local purchase decisions.",
                "word_count": [100, 180],
                "keyword_slot": "none",
                "heading_level": "h2",
                "prompt_rules": (
                    "Write 1 to 2 testimonials or result statements from local clients. "
                    "If quotes are provided in the brief, use them exactly with name and location attribution. "
                    "If not, write realistic placeholders with [Name], [Company or suburb], and a specific before/after result. "
                    "Reference the location in the attribution where natural. "
                    "Quote should name a tangible result, not generic praise. "
                    "No em dashes."
                ),
            },
            {
                "name": "faq",
                "label": "Frequently Asked Questions",
                "purpose": "Answer common local questions including service area, availability, pricing, and local-specific concerns.",
                "word_count": [200, 300],
                "keyword_slot": "lsi",
                "heading_level": "h2",
                "prompt_rules": (
                    "Write 4 to 5 FAQ items using PAA questions and common local service queries. "
                    "Include location-specific questions: coverage area, response time, local licensing, or area-specific process differences. "
                    "Each answer: 2 to 3 direct sentences. "
                    "Format: Question on its own line, then answer paragraph. "
                    "Include LSI keywords naturally. No em dashes."
                ),
            },
            {
                "name": "cta",
                "label": "Contact Us in [Location]",
                "purpose": "Location-aware CTA. Makes contacting the local team feel easy and natural.",
                "word_count": [60, 100],
                "keyword_slot": "none",
                "heading_level": "h2",
                "prompt_rules": (
                    "Write a short local CTA: H2 naming the action and location, then 1 to 2 sentences. "
                    "Reference the location naturally: 'Get a free quote from our [City] team today.' "
                    "Include a phone call, quote request, or booking as the primary action. "
                    "Keep the tone friendly and local, not corporate. "
                    "No em dashes. No exclamation marks."
                ),
            },
        ],
    },

    # ── ABOUT US PAGE ─────────────────────────────────────────────────────────
    "about_us": {
        "name": "About Us Page",
        "page_type": "about",
        "description": "Trust and credibility page. Starts with the reader's perspective, not the brand's story. Builds connection before credentials. Ends with a natural next step.",
        "sections": [
            {
                "name": "reader_first",
                "label": "Who This Is For",
                "purpose": "Open with the reader's situation, not the company's story. Shows you understand what they are looking for before talking about yourself.",
                "word_count": [100, 160],
                "keyword_slot": "none",
                "heading_level": "none",
                "prompt_rules": (
                    "Open the About page with 2 to 3 sentences that speak to the reader's situation, not the company. "
                    "Describe what they are trying to solve, what they are looking for in a working relationship, or what has not been working for them. "
                    "This is not a company introduction. This is an acknowledgement of the reader's reality. "
                    "Example opener style: 'You know your product is good. What you need is a partner who can communicate that to the right audience without the jargon.' "
                    "Tone: grounded, direct, confident. No hype. No em dashes."
                ),
            },
            {
                "name": "company_story",
                "label": "Our Story",
                "purpose": "Company origin and background. Focused on what matters to clients: why this business exists, what drives it, and what it specialises in.",
                "word_count": [180, 280],
                "keyword_slot": "supporting",
                "heading_level": "h2",
                "prompt_rules": (
                    "Write the company story focused on the things clients care about, not a full history. "
                    "Cover: why the business was founded or why the team does this work, what the business specialises in, and how the approach has been shaped by real client experience. "
                    "If a founding story or background is provided in the brief, use it. If not, write a credible placeholder. "
                    "Do not list every job the founders ever had. Focus on what is relevant to the client. "
                    "Tone: thoughtful, authentic, specific. "
                    "Include the supporting keyword naturally. No em dashes."
                ),
            },
            {
                "name": "mission_values",
                "label": "How We Work",
                "purpose": "Articulate the approach, values, or working philosophy that shapes every client engagement.",
                "word_count": [150, 240],
                "keyword_slot": "none",
                "heading_level": "h2",
                "prompt_rules": (
                    "Write 2 to 3 paragraphs or 3 value blocks describing the working approach. "
                    "Focus on what it is like to work with this business: the process, the communication style, the things they will and will not do. "
                    "H2 should reflect the approach, not be a generic 'Our Values' heading. "
                    "Examples: 'We work in focused sprints, not slow retainers.' / 'Every recommendation comes with the data behind it.' "
                    "Values must be specific and feel earned, not aspirational filler. "
                    "No em dashes. No exclamation marks."
                ),
            },
            {
                "name": "credibility",
                "label": "Experience and Track Record",
                "purpose": "Credential signals. Years in business, client count, industries served, notable achievements. Woven in naturally, not listed aggressively.",
                "word_count": [120, 200],
                "keyword_slot": "supporting",
                "heading_level": "h2",
                "prompt_rules": (
                    "Write a paragraph or short list of credibility signals. "
                    "Include where relevant and available: years in business, number of clients or projects, industries served, geographic coverage, key partnerships, or certifications. "
                    "If specific numbers are provided in the brief, use them. If not, write placeholder-format: 'Over [X] years working with [industry] businesses across [region].' "
                    "Weave this in naturally. Do not make it feel like a resume or a trophy wall. "
                    "Include supporting keyword. No em dashes."
                ),
            },
            {
                "name": "team",
                "label": "The Team",
                "purpose": "Brief human element. Who is behind the business. Reinforces the personal connection.",
                "word_count": [100, 160],
                "keyword_slot": "none",
                "heading_level": "h2",
                "prompt_rules": (
                    "Write a short paragraph introducing the team or the founders. "
                    "Focus on the combination of experience and approach that makes the team effective for clients. "
                    "If specific team details are provided in the brief, use them. If not, write a credible placeholder. "
                    "Keep the tone human and specific. Avoid the corporate bio format. "
                    "Example: 'Our team of [X] combines [background 1] and [background 2] — which means [what that combination delivers for clients].' "
                    "No em dashes."
                ),
            },
            {
                "name": "cta",
                "label": "Work With Us",
                "purpose": "A gentle, natural next step. Invites the right person to get in touch without pressure.",
                "word_count": [60, 100],
                "keyword_slot": "none",
                "heading_level": "h2",
                "prompt_rules": (
                    "Write a short closing CTA paragraph. "
                    "Invite the right kind of client to get in touch or explore services. "
                    "Tone: warm and specific, not corporate. "
                    "Example: 'If you are looking for a [service] partner who [specific quality], we would like to hear about what you are building.' "
                    "No em dashes. No exclamation marks. No 'ready to transform your business?' language."
                ),
            },
        ],
    },

    # ── CONTACT US PAGE ───────────────────────────────────────────────────────
    "contact_us": {
        "name": "Contact Us Page",
        "page_type": "contact",
        "description": "Short, functional contact page. Makes the first step feel easy and low-commitment. Sets clear expectations. Pre-contact FAQ removes objections before the phone rings.",
        "sections": [
            {
                "name": "intro",
                "label": "Get in Touch",
                "purpose": "Warm, approachable opener. Makes reaching out feel natural. Sets the tone for the conversation.",
                "word_count": [60, 100],
                "keyword_slot": "primary",
                "heading_level": "h1",
                "prompt_rules": (
                    "Write the page H1 and a 1 to 2 sentence intro paragraph. "
                    "H1 should be warm and direct: 'Get in Touch', 'Let's Talk', 'Contact [Brand]', or similar. Include primary keyword if it fits naturally. "
                    "Intro should feel personal and low-pressure. "
                    "Example: 'Have a project in mind or questions about working together? We would love to hear from you.' "
                    "Do not write the contact form itself. Just the intro copy. "
                    "No em dashes. No exclamation marks."
                ),
            },
            {
                "name": "expectations",
                "label": "What Happens Next",
                "purpose": "Set expectations on response time and what the client should expect after reaching out. Reduces uncertainty.",
                "word_count": [80, 140],
                "keyword_slot": "none",
                "heading_level": "h2",
                "prompt_rules": (
                    "Write a short paragraph setting response expectations. "
                    "Include: how quickly they can expect a response (e.g. within 1 business day), what the next step looks like (a call, a proposal, a discovery session), and any helpful context about what to include in their message. "
                    "If a Calendly or booking link is mentioned in the brief, reference it as an option. "
                    "Tone: helpful and professional, not corporate. "
                    "No em dashes."
                ),
            },
            {
                "name": "contact_methods",
                "label": "Other Ways to Reach Us",
                "purpose": "Alternative contact options for people who prefer not to use a form. Phone, email, office location, hours.",
                "word_count": [80, 140],
                "keyword_slot": "none",
                "heading_level": "h2",
                "prompt_rules": (
                    "Write a short block listing available contact methods and office information. "
                    "Include placeholders for: phone number, email address, office address (if applicable), and business hours. "
                    "Format as short readable lines, not a dense paragraph. "
                    "If physical address is provided in the brief, use it. If not, use [Address] as placeholder. "
                    "For local businesses, note: 'Our [City] office is open [hours].' "
                    "No em dashes."
                ),
            },
            {
                "name": "pre_contact_faq",
                "label": "Before You Get in Touch",
                "purpose": "2 to 4 short FAQ items that answer the questions people have right before contacting. Reduces drop-off at the contact page.",
                "word_count": [150, 250],
                "keyword_slot": "lsi",
                "heading_level": "h2",
                "prompt_rules": (
                    "Write 3 to 4 FAQ items that address common pre-contact questions. "
                    "Good topics: minimum project size or budget, how to prepare for the first conversation, what types of clients the business works with, whether they serve a certain industry or geography, what to expect from the first call. "
                    "Each answer: 2 to 3 sentences. Direct and specific. "
                    "Include LSI keywords naturally. "
                    "Format: Question as H3, then answer paragraph. "
                    "No em dashes."
                ),
            },
        ],
    },

    # ── PRODUCT PAGE (ECOMMERCE) ──────────────────────────────────────────────
    "product_page": {
        "name": "Product Page",
        "page_type": "product",
        "description": "Ecommerce product detail page (PDP). Leads with benefits then supports with features. Keyword in product name H1. Specifications, use cases, social proof, and FAQ. Optimised for Product schema.",
        "sections": [
            {
                "name": "product_intro",
                "label": "Product Introduction",
                "purpose": "Product name as H1 with key attribute. Short benefit-led description: what it does for the customer and why it matters. Primary keyword in first sentence.",
                "word_count": [80, 150],
                "keyword_slot": "primary",
                "heading_level": "h1",
                "prompt_rules": (
                    "Write the product H1 and a short intro description (2 to 3 sentences). "
                    "H1: product name plus one key attribute. Example: 'Blue Linen Blazer — Women's Summer Essential'. Include primary keyword. "
                    "Intro description: lead with what the product does for the customer. Then support with 1 key quality signal. "
                    "Focus on the customer outcome first, product details second. "
                    "Example: 'The [Product] takes [problem] off your plate with [benefit]. Made from [material], it [quality signal].' "
                    "No em dashes. No exclamation marks. No manufacturer-style copy."
                ),
            },
            {
                "name": "benefits_features",
                "label": "Key Features and Benefits",
                "purpose": "Benefit-led bullet list followed by technical specifications. Benefits first — what it does for the buyer. Specs second — the proof behind the benefits.",
                "word_count": [200, 350],
                "keyword_slot": "supporting",
                "heading_level": "h2",
                "prompt_rules": (
                    "Write two subsections: "
                    "1. Benefits (H3): 4 to 6 bullet points. Each bullet leads with a benefit, then supports it with a feature. Format: '[Benefit]: [Feature that delivers it].' Example: 'Stays cool all day: breathable 100% linen construction.' "
                    "2. Specifications (H3): scannable list of technical details. Include relevant details from the brief: dimensions, materials, weight, compatibility, certifications, variants. If specs are not provided, write realistic placeholders. "
                    "Include supporting keyword naturally in the benefits section. "
                    "No em dashes. No exclamation marks."
                ),
            },
            {
                "name": "use_cases",
                "label": "Who It's For",
                "purpose": "Help the shopper see themselves using this product. Use cases and ideal customer scenarios.",
                "word_count": [120, 200],
                "keyword_slot": "supporting",
                "heading_level": "h2",
                "prompt_rules": (
                    "Write 2 to 3 paragraphs or a short structured block describing who this product is for and in what situations they would use it. "
                    "Be specific about the customer profile and the use scenario. "
                    "Examples: 'Ideal for B2B teams who need X without Y.' / 'Perfect for the daily commuter who wants X but not Y.' "
                    "If PAA data includes questions about who should use this or what it is best for, incorporate those answers here. "
                    "No em dashes. Keep it focused on the buyer."
                ),
            },
            {
                "name": "social_proof",
                "label": "What Customers Say",
                "purpose": "Customer review highlights. Social proof that validates product claims and builds purchase confidence.",
                "word_count": [100, 180],
                "keyword_slot": "none",
                "heading_level": "h2",
                "prompt_rules": (
                    "Write 1 to 2 customer review highlights or a summary of review sentiment. "
                    "If reviews are provided in the brief, use them exactly with attribution. "
                    "If not, write realistic placeholders: quoted review with [Customer Name, location or verified buyer] and a specific outcome or detail mentioned. "
                    "Include a rating reference if available: 'Rated 4.8 stars across [X] reviews.' "
                    "Reviews should mention specific product details, not generic praise. "
                    "No em dashes."
                ),
            },
            {
                "name": "faq",
                "label": "Frequently Asked Questions",
                "purpose": "Answer real pre-purchase questions from customers. Pulls from PAA data. Reduces hesitation and return rates.",
                "word_count": [180, 300],
                "keyword_slot": "lsi",
                "heading_level": "h2",
                "prompt_rules": (
                    "Write 3 to 5 FAQ items using PAA questions and common product purchase objections. "
                    "Good topics: sizing/compatibility, shipping and returns, how to use the product, care instructions, warranty, differences between variants. "
                    "Each answer: 2 to 3 direct sentences. "
                    "Include LSI keywords naturally. "
                    "Format: Question as H3, then answer paragraph. "
                    "No em dashes."
                ),
            },
        ],
    },

    # ── COLLECTION / CATEGORY PAGE (ECOMMERCE) ───────────────────────────────
    "collection_page": {
        "name": "Collection / Category Page",
        "page_type": "collection",
        "description": "Ecommerce category page copy. SEO-rich intro, buying guide, subcategory navigation copy, brand value in this category, and FAQ. Balances search intent with purchase guidance. Optimised for CollectionPage schema.",
        "sections": [
            {
                "name": "category_intro",
                "label": "Category Introduction",
                "purpose": "SEO-rich category description. Keyword in H1 and opening paragraph. Sets the category context and signals topical authority to search engines.",
                "word_count": [120, 200],
                "keyword_slot": "primary",
                "heading_level": "h1",
                "prompt_rules": (
                    "Write the category H1 and an intro paragraph (3 to 4 sentences). "
                    "H1: clear, keyword-rich category name. Example: 'Women's Linen Blazers' or 'Industrial Safety Equipment'. "
                    "Intro paragraph: describe the category, who it is for, and the primary purchase intent. Include the primary keyword in the first sentence. "
                    "Do not write generic category filler. Show category expertise: reference what makes a good product in this category, who it is for, or what problem it solves. "
                    "Avoid promotional tone. This is editorial, not advertising. "
                    "No em dashes."
                ),
            },
            {
                "name": "buying_guide",
                "label": "How to Choose",
                "purpose": "Short buying guide that helps shoppers make a better decision. Demonstrates category expertise and builds topical authority. Reduces bounce and increases time on page.",
                "word_count": [200, 320],
                "keyword_slot": "supporting",
                "heading_level": "h2",
                "prompt_rules": (
                    "Write a practical buying guide for this product category. "
                    "Cover 3 to 4 decision criteria that matter most when choosing from this category. "
                    "Each criterion: a short bold label and 2 to 3 sentences of practical guidance. "
                    "Examples: 'Size and Fit', 'Material and Durability', 'Budget Range', 'Use Case', 'Compatibility'. "
                    "H2 should be a question or directive: 'How to Choose the Right [Category]' or 'What to Look For'. "
                    "Include supporting keyword naturally. "
                    "Tone: helpful and authoritative, like a knowledgeable colleague. No em dashes."
                ),
            },
            {
                "name": "subcategory_overview",
                "label": "What's in This Collection",
                "purpose": "Brief overview of key subcategories or product types within this collection. Helps navigation and signals category depth to search engines.",
                "word_count": [150, 250],
                "keyword_slot": "supporting",
                "heading_level": "h2",
                "prompt_rules": (
                    "Write a short overview of 3 to 5 key product types or subcategories within this collection. "
                    "Each type: a bold label and 1 to 2 sentences describing it and who it is for. "
                    "This signals category breadth and helps shoppers navigate to the right product group. "
                    "Use natural internal link anchor language where appropriate. "
                    "Include supporting keyword naturally. "
                    "No em dashes."
                ),
            },
            {
                "name": "brand_value",
                "label": "Why Shop With Us",
                "purpose": "Brand value proposition in the context of this category. 3 specific differentiators: quality, curation, service, or expertise.",
                "word_count": [150, 240],
                "keyword_slot": "none",
                "heading_level": "h2",
                "prompt_rules": (
                    "Write 3 short differentiator blocks relevant to this specific category. "
                    "Each block: a bold label (3 to 5 words), then 2 sentences. "
                    "Good differentiators for ecommerce: curated selection, quality standards, free returns, fast delivery, expert sourcing, or category-specific guarantees. "
                    "Avoid generic differentiators that could apply to any ecommerce store. "
                    "Tie each differentiator to what it means for the buyer of this specific category. "
                    "No em dashes."
                ),
            },
            {
                "name": "faq",
                "label": "Frequently Asked Questions",
                "purpose": "Category-level FAQs. Common pre-purchase questions about this product type. Helps SEO via PAA visibility and reduces pre-purchase drop-off.",
                "word_count": [200, 320],
                "keyword_slot": "lsi",
                "heading_level": "h2",
                "prompt_rules": (
                    "Write 4 to 5 FAQ items using PAA questions and common category-level purchase questions. "
                    "Good topics: how to choose between product types in this category, size/compatibility/fit guidance, care and maintenance, delivery and returns policy, price range expectations. "
                    "Each answer: 2 to 3 direct sentences. "
                    "Include LSI keywords naturally. "
                    "Format: Question as H3, then answer paragraph. "
                    "No em dashes."
                ),
            },
        ],
    },

}


def get_templates_for_page_type(page_type: str) -> dict:
    """Returns all templates matching a given page_type."""
    return {k: v for k, v in TEMPLATES.items() if v["page_type"] == page_type}


def get_template(template_key: str) -> dict:
    """Returns a single template by key. Raises if not found."""
    if template_key not in TEMPLATES:
        raise ValueError(f"Template '{template_key}' not found.")
    return TEMPLATES[template_key]


def parse_custom_template(raw_text: str, page_type: str = "blog") -> dict:
    """
    Parses a custom template from user text input.
    Format expected (one per line):
        Section Name | min_words-max_words
    Example:
        Introduction | 100-160
        How It Works | 200-300
        Case Studies | 200-280
        FAQ | 150-250
        Next Steps | 60-100

    Returns a template dict compatible with the registry format.
    """
    sections = []
    for line in raw_text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("|")
        section_name = parts[0].strip()
        word_range = [150, 250]
        if len(parts) > 1:
            range_part = parts[1].strip()
            try:
                low, high = range_part.split("-")
                word_range = [int(low.strip()), int(high.strip())]
            except Exception:
                pass

        name_slug = section_name.lower().replace(" ", "_")
        is_faq = "faq" in name_slug or "question" in name_slug
        is_cta = any(x in name_slug for x in ["cta", "next step", "contact", "get in touch"])
        is_intro = name_slug in ("intro", "introduction", "overview") or sections == []

        sections.append({
            "name": name_slug,
            "label": section_name,
            "purpose": f"Cover the topic: {section_name}",
            "word_count": word_range,
            "keyword_slot": "primary" if is_intro else ("lsi" if is_faq else "supporting"),
            "heading_level": "none" if is_intro else "h2",
            "prompt_rules": (
                "Answer 3 to 5 reader questions using PAA data provided. 2 to 4 sentences each." if is_faq
                else "Short closing paragraph with a natural next action. Business-type-aware. No em dashes." if is_cta
                else f"Write the {section_name} section. Be specific and useful. No em dashes."
            ),
        })

    return {
        "name": "Custom Template",
        "page_type": page_type,
        "description": "User-defined section structure.",
        "sections": sections,
    }
