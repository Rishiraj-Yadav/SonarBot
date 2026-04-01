# ML Tool Router Integration: Technical Analysis

The SonarBot agent is heavily tool-reliant, featuring over 18 distinct tools (sub-agents, web browsers, sandboxes, github/gmail integrations, etc.). This document provides a detailed breakdown of the **Local ML Tool Router** we implemented in Phase 1, analyzing why it was necessary, how it operates, and the mathematical algorithms powering it.

---

## 1. Why Is It There? (The Problem)

### Context Window & Token Bloat
LLMs operate on stateless HTTP requests. Every time SonarBot communicates with the Gemini model, it must send the complete chat history *along with the full JSON schemas of every tool it can use*.

Before the Tool Router:
- **18+ Tool Schemas** were packed into the system payload on *every single request*.
- This cost roughly **~2,000 to ~4,000 input tokens** purely on schema definitions per turn.
- A user saying something as simple as *"What time is it?"* would unnecessarily load the schemas for `docker_sandbox`, `github_list_repos`, and `browser_click`.

### The Impact
1. **Financial Cost:** Massive API bills scaling linearly with every message due to redundant token ingestion.
2. **Latency Limitations:** Sending massive json payloads over the network takes time.
3. **Model Confabulation (Hallucinations):** Providing complex, unrelated tools to the model increases the mathematical probability of it deciding to use a wrong tool. If you ask about the weather and it sees the `run_command` tool, it might erroneously try to curl a weather API instead of using the `search_web` tool it was built for.

---

## 2. Which Algorithms Did We Use?

Because the routing happens on every single turn, the model **must** be hyper-fast and CPU-friendly. A deep neural network (like BERT) would take several hundred milliseconds and eat up RAM, causing noticeable stuttering before the agent even queried Gemini. 

Instead, we utilized a highly optimized, classical Machine Learning pipeline using `scikit-learn`:

### A. Feature Extraction: Term Frequency-Inverse Document Frequency (TF-IDF)
**Algorithm:** `TfidfVectorizer(ngram_range=(1, 2))`
* **What it does:** It converts the raw text of a user's prompt (e.g., "search for flights") into a mathematical array of numbers (a vector). 
* **Why TF-IDF?** Unlike simple word counting, TF-IDF weighs the words based on how rare they are. It knows that words like "search" and "flights" hold significantly more predictive power than common words like "for". By setting `ngram_range=(1,2)`, the model calculates probabilities not just for single words, but for two-word pairings (Bigrams) like "search local" vs "search web".

### B. Classification: Logistic Regression via One-vs-Rest (OvR)
**Algorithm:** `OneVsRestClassifier(LogisticRegression(class_weight="balanced"))`
* **The Goal:** A single user prompt might require *multiple* tools simultaneously (e.g., "search the web for python guides and write it to a file" requires both `search_web` and `write_file`).
* **Why Logistic Regression?** It is incredibly fast, returning results in under `1ms`.
* **How One-Vs-Rest Works:** Standard Logistic Regression only picks a single winner. `OneVsRestClassifier` builds a dedicated binary logistic model for *each distinct tool*. 
  * Model 1 asks: *"Does this need the `search_web` tool? Yes/No."*
  * Model 2 asks: *"Does this need the `read_file` tool? Yes/No."* 
  * It combines these independent predictions to output a multi-label array of all required tools.

---

## 3. How Is It Implemented? (The Architecture)

The system is broken into three distinct layers to ensure it acts as a non-blocking interceptor.

### 1. The Bootstrap Training Pipeline (`train_tool_router.py`)
If no model exists on your machine, the system cannot function. We provided a `bootstrap_tools.csv` containing common mappings (e.g., `"remind me to buy milk" -> "memory_write"`). The pipeline reads this CSV, learns the mathematical weights associating specific words to specific tools, strings the `TfidfVectorizer` and `LogisticRegression` together into a unified Scikit-Learn `Pipeline`, and dumps the trained binary to `~/.assistant/ml_models/tool_router.joblib`.

### 2. The Interceptor (`tool_router.py`)
This class lives in memory while the server runs. It pre-loads the `.joblib` model. It has a `shadow_mode` variable (controlled via `config.toml`). When shadow mode is false, it actively intercepts traffic.

### 3. The Injection (`loop.py`)
In the core `AgentLoop`, right before `GeminiProvider.complete(...)` is called, the loop intercepts the latest user message and queries the `ToolRouter`.
```python
# loop.py conceptual flow
latest_user_message = extract_latest_message()
all_schemas = tool_registry.get_tools_schema()

# The ML magic
filtered_schemas, metrics = tool_router.select_tools(latest_user_message, all_schemas)

# We now send ONLY the required schemas to Gemini (e.g., 2 schemas instead of 18)
response = gemini.complete(..., tools=filtered_schemas)
```

### Safety Nets
Machine Learning is inherently probabilistic. If a user asks something deeply convoluted, the router might fail to assign a tool.

To prevent the agent from breaking, we implemented two safety nets:
1. **Core Tool Whitelist:** Tools like `llm_task` (sub-agents) and basic file IO are *always* appended to the final list, regardless of what the model predicts.
2. **Confidence Thresholds:** If the Logistic Regression yields a confidence probability lower than your configured `min_confidence = 0.45`, the router triggers a "fallback" and passes *all* tools through, ensuring the agent never gets paralyzed by an uncertain ML prediction.
