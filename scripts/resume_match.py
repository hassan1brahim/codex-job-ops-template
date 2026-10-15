#!/usr/bin/env python3
"""Score starter resumes against a job description by weighted hard-skill coverage."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STARTER_DIR = ROOT / "profile" / "starter-resumes"
MATCH_THRESHOLD = 0.80
MIN_JD_SKILLS = 4
MAX_WEIGHT = 3

# canonical skill -> aliases. An alias containing a capital letter matches case-sensitively, so
# "REST" or "Go" never match "the rest" or "go to market".
SKILLS: dict[str, tuple[str, ...]] = {
    # Languages
    "Python": ("python",),
    "Java": ("java",),
    "JavaScript": ("javascript", "JS", "ecmascript"),
    "TypeScript": ("typescript", "TS"),
    "C++": ("c++", "cpp"),
    "C": ("C",),
    "C#": ("c#",),
    "Go": ("Go", "golang"),
    "Rust": ("Rust",),
    "Kotlin": ("kotlin",),
    "Swift": ("Swift",),
    "Ruby": ("Ruby",),
    "PHP": ("php",),
    "Scala": ("scala",),
    "R": ("R",),
    "SQL": ("sql",),
    "Bash": ("Bash", "shell scripting"),
    "HTML": ("html", "html5"),
    "CSS": ("css", "css3"),
    # Frontend
    "React": ("react", "react.js", "reactjs"),
    "React Native": ("react native",),
    "Next.js": ("next.js", "nextjs"),
    "Vue": ("vue", "vue.js"),
    "Angular": ("angular",),
    "Svelte": ("svelte",),
    "Redux": ("redux",),
    "Tailwind CSS": ("tailwind", "tailwind css"),
    "Vite": ("vite",),
    "Webpack": ("webpack",),
    "Accessibility": ("accessibility", "a11y", "wcag"),
    "Responsive design": ("responsive design", "responsive web", "responsive UI", "mobile-first"),
    "PWA": ("pwa", "progressive web app", "service worker", "service workers"),
    # Backend
    "Node.js": ("node.js", "nodejs", "Node"),
    "Express": ("express.js", "expressjs"),
    "NestJS": ("nestjs", "nest.js"),
    "Django": ("django",),
    "Flask": ("flask",),
    "FastAPI": ("fastapi",),
    "Spring Boot": ("spring boot",),
    ".NET": (".net", "asp.net"),
    "REST APIs": ("REST", "restful", "rest api", "rest apis"),
    "GraphQL": ("graphql",),
    "gRPC": ("grpc",),
    "Microservices": ("microservices", "microservice"),
    "WebSockets": ("websocket", "websockets"),
    "Authentication": ("authentication", "authorization", "oauth", "jwt"),
    "Prisma": ("prisma",),
    # Data stores
    "PostgreSQL": ("postgresql", "postgres"),
    "MySQL": ("mysql",),
    "SQLite": ("sqlite",),
    "MongoDB": ("mongodb", "mongo"),
    "Redis": ("redis",),
    "DynamoDB": ("dynamodb",),
    "Elasticsearch": ("elasticsearch",),
    "Snowflake": ("snowflake",),
    "NoSQL": ("nosql",),
    "Relational databases": ("relational database", "relational databases", "rdbms"),
    "pgvector": ("pgvector",),
    "Vector databases": ("vector database", "vector databases", "vector store", "faiss", "chromadb",
                         "pinecone"),
    # Cloud / DevOps
    "AWS": ("aws", "amazon web services"),
    "Amazon Bedrock": ("bedrock",),
    "AWS Lambda": ("aws lambda", "Lambda"),
    "GCP": ("gcp", "google cloud"),
    "Azure": ("azure",),
    "Docker": ("docker", "containers", "containerization"),
    "Kubernetes": ("kubernetes", "k8s"),
    "Terraform": ("terraform",),
    "CI/CD": ("ci/cd", "continuous integration", "continuous delivery", "continuous deployment"),
    "GitHub Actions": ("github actions",),
    "Git": ("git", "github", "version control"),
    "Linux": ("linux", "unix"),
    "Vercel": ("vercel",),
    "Monitoring": ("monitoring", "observability", "logging"),
    "Kafka": ("kafka",),
    "Serverless": ("serverless",),
    # Testing / practice
    "Unit testing": ("unit testing", "unit tests", "unit test"),
    "Automated testing": ("automated testing", "test automation", "automated tests"),
    "Jest": ("jest",),
    "Vitest": ("vitest",),
    "PyTest": ("pytest",),
    "Playwright": ("playwright",),
    "Selenium": ("selenium",),
    "Debugging": ("debugging", "debug"),
    "Code review": ("code review", "code reviews"),
    "Agile": ("agile", "scrum"),
    "Data structures": ("data structures",),
    "Algorithms": ("algorithms",),
    "Object-oriented programming": ("object-oriented", "object oriented", "oop"),
    "System design": ("system design", "distributed systems", "scalable systems"),
    "Concurrency": ("concurrency", "asynchronous", "async", "multithreading"),
    "Networking": ("networking", "tcp/ip", "mqtt"),
    "Embedded systems": ("embedded systems", "embedded software", "firmware", "microcontroller",
                         "microcontrollers", "RTOS"),
    "Memory management": ("memory management",),
    # AI / ML / data
    "Machine learning": ("machine learning", "ml"),
    "Deep learning": ("deep learning", "neural network", "neural networks"),
    "PyTorch": ("pytorch",),
    "TensorFlow": ("tensorflow",),
    "scikit-learn": ("scikit-learn", "sklearn"),
    "Pandas": ("pandas",),
    "NumPy": ("numpy",),
    "Jupyter": ("jupyter",),
    "NLP": ("nlp", "natural language processing"),
    "Computer vision": ("computer vision", "image classification"),
    "LLMs": ("llm", "llms", "large language model", "large language models"),
    "Generative AI": ("generative ai", "genai", "gen ai"),
    "RAG": ("rag", "retrieval-augmented generation", "retrieval augmented generation"),
    "AI agents": ("ai agent", "ai agents", "llm agents", "agentic", "multi-agent", "autonomous agents",
                  "agent framework", "agent workflows"),
    "Tool calling": ("tool calling", "tool use", "function calling"),
    "Prompt engineering": ("prompt engineering", "prompting", "prompts"),
    "Embeddings": ("embedding", "embeddings", "sentence transformers"),
    "Hugging Face": ("hugging face", "huggingface", "transformers"),
    "LangChain": ("langchain",),
    "MCP": ("mcp", "model context protocol"),
    "OpenAI API": ("openai",),
    "Fine-tuning": ("fine-tuning", "fine-tune", "fine-tuned", "finetuning"),
    "Model evaluation": ("model evaluation", "evaluation metrics", "evals", "benchmarking"),
    "Classification": ("classification", "classifier", "classifiers"),
    "Clustering": ("clustering",),
    "ONNX": ("onnx",),
    "MLOps": ("mlops",),
    "Data pipelines": ("data pipeline", "data pipelines", "etl", "elt"),
    "Data analysis": ("data analysis", "exploratory data analysis", "eda", "analytics"),
    "Data visualization": ("data visualization", "matplotlib", "tableau", "power bi"),
    "Statistics": ("statistics", "statistical"),
    "A/B testing": ("a/b testing", "a/b tests", "experimentation"),
    "Spark": ("Spark", "pyspark"),
    "Airflow": ("airflow",),
    "Web scraping": ("web scraping", "scraping", "crawler", "crawling"),
    # Less common stacks, so a JD naming them is scored honestly as a gap
    "Elixir": ("elixir",),
    "Temporal": ("Temporal",),
    "Haskell": ("haskell",),
    "MATLAB": ("matlab",),
    "CUDA": ("cuda",),
    "ROS": ("ROS",),
    "Unity": ("Unity",),
    "Unreal Engine": ("unreal engine", "Unreal"),
    "Hadoop": ("hadoop",),
    "BigQuery": ("bigquery",),
    "Databricks": ("databricks",),
    "dbt": ("dbt",),
    "Excel": ("Excel",),
    "Salesforce": ("salesforce",),
    "Grafana": ("grafana",),
    "Prometheus": ("Prometheus",),
    "RabbitMQ": ("rabbitmq",),
    "LlamaIndex": ("llamaindex",),
    "Figma": ("Figma",),
}
# Never count "C" when it is part of C++ / C# / C-level, or "R" in R&D.
_NOT_AFTER = r"(?<![A-Za-z0-9+#./&-])"
_NOT_BEFORE = r"(?![A-Za-z0-9+#&]|\.[A-Za-z])"


# Phrases where the capitalized word is ordinary English, not the skill.
_ENGLISH_AFTER = {"Go": r"(?!\s+(?:to|ahead|beyond|live|further|above)\b)"}


def _pattern(alias: str) -> re.Pattern[str]:
    flags = 0 if alias != alias.lower() else re.IGNORECASE
    guard = _ENGLISH_AFTER.get(alias, "")
    return re.compile(_NOT_AFTER + re.escape(alias) + _NOT_BEFORE + guard, flags)


_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    skill: [_pattern(alias) for alias in aliases]
    for skill, aliases in SKILLS.items()
}


def skill_counts(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for skill, patterns in _PATTERNS.items():
        # max, not sum: "REST" and "REST APIs" are two aliases for one mention.
        total = max(len(p.findall(text)) for p in patterns)
        if total:
            counts[skill] = total
    return counts


def resume_text(path: Path) -> str:
    """Visible-ish resume text: LaTeX comments (the keyword block) never count."""
    raw = path.read_text(encoding="utf-8")
    if path.suffix != ".tex":
        return raw
    lines = [re.sub(r"(?<!\\)%.*$", "", line) for line in raw.splitlines()]
    text = "\n".join(lines)
    text = re.sub(r"\\(?:href)\{[^}]*\}", " ", text)
    text = re.sub(r"\\[a-zA-Z]+\*?", " ", text)
    return text.replace("\\", " ").replace("{", " ").replace("}", " ")


@dataclass(frozen=True)
class MatchResult:
    resume: Path
    score: float
    matched: tuple[str, ...]
    missing: tuple[str, ...]


def score_resume(jd_counts: dict[str, int], text: str, resume: Path) -> MatchResult:
    have = set(skill_counts(text))
    weights = {skill: min(count, MAX_WEIGHT) for skill, count in jd_counts.items()}
    total = sum(weights.values()) or 1
    matched = sorted((s for s in weights if s in have), key=lambda s: -weights[s])
    missing = sorted((s for s in weights if s not in have), key=lambda s: -weights[s])
    score = sum(weights[s] for s in matched) / total
    return MatchResult(resume, score, tuple(matched), tuple(missing))


# Role words in the JD title -> the starter built for that role. Order matters: first hit wins.
TITLE_HINTS: tuple[tuple[str, str], ...] = (
    (r"machine learning|\bML\b|deep learning|computer vision|data scien", "SWE_ML"),
    (r"\bAI\b|agent|LLM|generative", "SWE_AI_Agents"),
    (r"c\+\+|embedded|firmware|low[\s-]?level|systems (software|programming|engineer)", "SWE_Systems_CPP"),
    (r"full[\s-]?stack|back[\s-]?end|front[\s-]?end|react|javascript|web|python", "SWE_Full_Stack"),
)
TITLE_TIE_BREAK = 0.05


def title_hint(jd_text: str) -> str | None:
    """Starter stem suggested by the JD's first heading, e.g. '# Ramp — Software Engineer - Backend'."""
    title = next((line for line in jd_text.splitlines() if line.startswith("#")), "")
    for pattern, stem in TITLE_HINTS:
        if re.search(pattern, title, re.IGNORECASE):
            return stem
    return None


def rank_starters(jd_text: str, starter_dir: Path = STARTER_DIR) -> tuple[dict[str, int], list[MatchResult]]:
    """Rank by score; the starter named by the JD title wins ties within 5 points."""
    jd_counts = skill_counts(jd_text)
    hint = title_hint(jd_text)
    results = [
        score_resume(jd_counts, resume_text(path), path)
        for path in sorted(starter_dir.glob("*.tex"))
    ]
    results.sort(key=lambda r: -(r.score + (TITLE_TIE_BREAK if r.resume.stem == hint else 0)))
    return jd_counts, results
