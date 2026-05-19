# Cold email to Neel Nanda — Draft

**Status:** ready to send after one read-through. Edit anything that
doesn't sound like you. Do NOT include your university email anywhere
in the message body or in your "From" address. Use a personal email.

**Send when:** after you have at least 10 hand-annotated agent failures
captured by Loupe (the substance of the "early signal" line below).

---

## Subject line — pick one

- `Open-source agent forensics tool — wanted your read before I write it up`
- `Built an interpretability-flavored agent observability tool — 10 min of your time?`
- `Forensic observability for LLM agents — would value your feedback`

The first is the strongest. It hooks on "before I write it up" which
signals you're treating him as a peer reviewer, not asking for a job.

---

## Body

> Hi Neel,
>
> I'm Yashwanth — final-year undergrad, building an open-source tool
> called **Loupe**: a forensic observability + interpretability layer
> for LLM agents. Two-line install, captures every step of an agent
> run, lets you tag failures into a benchmark, and is structured so
> that circuit-attribution data (SAE feature activations per failing
> step) can be layered on top later.
>
> Repo: https://github.com/YashwanthKamireddi/loupe
>
> The reason I'm writing: my v0.2 plan is exactly the interpretability
> bridge — attach SAE feature activations to flagged failure steps so
> an agent debugging session is also a mechanistic-interp foothold.
> Before I sink three months into that, I wanted to ask: **is there a
> sharper version of that idea you've seen people miss?**
>
> Specifically, two questions if you have a moment:
>
> 1. For an agent failure (e.g. an LLM loop, a wrong tool-call), what
>    SAE features would you actually want surfaced — top-K by
>    activation? top-K by deviation from a non-failing baseline? full
>    distribution over a relevant feature subset?
>
> 2. Is there an existing tool / dataset doing this end-to-end that I
>    should just contribute to instead of duplicating?
>
> Early signal so this isn't entirely hypothetical: I have N
> hand-tagged failures across {anthropic / openai / gemini} captured
> with Loupe, span of latencies / kinds attached — happy to send the
> JSONL bundle if useful.
>
> Either way, thank you for everything you've put into the field —
> ARENA + your write-ups are the single biggest reason I'm doing
> this work and not something safer.
>
> — Yashwanth Kamireddi
> github.com/YashwanthKamireddi
> [personal email — NOT the GITAM one]

---

## Checklist before hitting send

- [ ] Replace `N` with the actual count of hand-tagged failures
- [ ] Replace `{anthropic / openai / gemini}` with the actual providers
      you captured against
- [ ] Personal email in From, not the GITAM one (this matters — the
      forensics-leak point applies here too)
- [ ] No university affiliation anywhere in the body
- [ ] One-line proofread for any auto-corrected typos
- [ ] If you have a personal website, link it in the signature

## Why this email will land

- **Specific, not generic.** Two pointed technical questions. Easy to
  answer in 3 minutes.
- **You've done the work.** "Repo: https://..." with real code, real
  tests, real CI. No empty pitch.
- **Concrete signal.** "Hand-tagged failures, JSONL bundle ready." This
  is what separates serious people from time-wasters.
- **Frames him as a peer reviewer.** Not "give me a job", not "what
  should I work on" — "is the idea I already have sharper than I think?"
- **Honest credit.** ARENA mention is real and short. No fanboy energy.

## Cold-email hygiene

- **Send weekday morning his timezone** (London — early UK time is best
  catch rate for tech researchers).
- **No follow-up for 14 days.** If silence after 14, one polite bump:
  "circling back in case this got buried — totally fine if not the
  right time."
- **If he replies asking for the JSONL bundle:** generate it with
  `loupe export --out loupe-bench-v0.jsonl` and attach. That artifact
  is the thing that proves this is real.
