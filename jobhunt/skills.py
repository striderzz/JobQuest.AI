"""Tech skill taxonomy shared by the resume parser and the job matcher.

Each entry is (canonical name, role, patterns). Role says which job family a
skill is evidence for: "sde", "ml", or "both" (general engineering skills that
count toward either). Patterns are regexes matched case-insensitively with
alphanumeric boundaries on both sides, except patterns prefixed with "(?-i)",
which are case-sensitive (used for short names like Go, R, C that collide with
ordinary English).

Matching the same taxonomy on both sides is what keeps the overlap honest:
"PyTorch" on the resume and "pytorch" in a posting land on one canonical name.
"""
import re

_LIST_CTX_BEFORE = r"(?:(?<=[,/(|:])|(?<=[,/(|:] )|(?<=\band )|(?<=\bor )|(?<=\bin ))"
_LIST_CTX_AFTER = r"(?=\s*[,/)|]|\s+(?:and|or)\b|\s*$)"


def _short(name):
    """Case-sensitive single-letter-ish language name, only inside list-like context."""
    return "(?-i)" + _LIST_CTX_BEFORE + re.escape(name) + _LIST_CTX_AFTER


TAXONOMY = [
    # ---- languages
    ("Python", "both", [r"python\d?"]),
    ("Java", "sde", [r"java(?!\s*script)", r"core java", r"j2ee"]),
    ("JavaScript", "sde", [r"javascript", r"(?-i)JS", r"es6"]),
    ("TypeScript", "sde", [r"typescript"]),
    ("C++", "both", [r"c\+\+", r"cpp"]),
    ("C", "sde", [_short("C"), r"c programming", r"(?-i)C(?=/C\+\+)"]),
    ("C#", "sde", [r"c#", r"c sharp"]),
    ("Go", "sde", [r"golang", _short("Go")]),
    ("Rust", "sde", [r"rust(?:lang)?"]),
    ("Kotlin", "sde", [r"kotlin"]),
    ("Swift", "sde", [r"swift", r"swiftui"]),
    ("Scala", "both", [r"scala"]),
    ("R", "ml", [_short("R"), r"r programming", r"rstudio"]),
    ("SQL", "both", [r"sql", r"t-sql", r"pl/sql"]),
    ("Bash / Shell", "sde", [r"bash", r"shell scripting", r"shell scripts?", r"powershell"]),
    ("PHP", "sde", [r"php"]),
    ("Ruby", "sde", [r"ruby"]),
    ("Dart", "sde", [r"dart"]),
    ("MATLAB", "ml", [r"matlab"]),
    ("HTML/CSS", "sde", [r"html5?", r"css3?", r"scss", r"sass"]),
    ("Solidity", "sde", [r"solidity"]),
    # ---- backend / web frameworks
    ("Spring Boot", "sde", [r"spring ?boot", r"spring framework", r"spring mvc"]),
    ("Django", "sde", [r"django"]),
    ("Flask", "both", [r"flask"]),
    ("FastAPI", "both", [r"fastapi", r"fast api"]),
    ("Node.js", "sde", [r"node\.?js"]),
    ("Express.js", "sde", [r"express\.js", r"expressjs"]),
    ("NestJS", "sde", [r"nest\.?js"]),
    (".NET", "sde", [r"\.net(?: core)?", r"asp\.net", r"dotnet"]),
    ("Ruby on Rails", "sde", [r"rails", r"ruby on rails"]),
    ("REST APIs", "sde", [r"rest ?apis?", r"restful(?: apis?| services)?", r"rest (?:web )?services"]),
    ("GraphQL", "sde", [r"graphql"]),
    ("gRPC", "sde", [r"grpc", r"protobuf", r"protocol buffers"]),
    ("Microservices", "sde", [r"micro-?services?"]),
    ("Kafka", "sde", [r"kafka"]),
    ("RabbitMQ", "sde", [r"rabbitmq", r"rabbit mq"]),
    ("Redis", "sde", [r"redis"]),
    ("Celery", "sde", [r"celery"]),
    # ---- frontend / mobile
    ("React", "sde", [r"(?-i)React(?:\.?js|JS)?(?! Native)", r"react\.?js"]),  # case-sensitive: "ReAct agents" is not React
    ("Next.js", "sde", [r"next\.?js"]),
    ("Angular", "sde", [r"angular(?:js)?"]),
    ("Vue", "sde", [r"vue(?:\.?js)?"]),
    ("Redux", "sde", [r"redux"]),
    ("Tailwind", "sde", [r"tailwind(?:css)?"]),
    ("Android", "sde", [r"android"]),
    ("iOS", "sde", [r"ios"]),
    ("Flutter", "sde", [r"flutter"]),
    ("React Native", "sde", [r"react native"]),
    # ---- databases / data engineering
    ("PostgreSQL", "both", [r"postgres(?:ql)?"]),
    ("MySQL", "both", [r"mysql"]),
    ("MongoDB", "both", [r"mongo(?:db)?"]),
    ("SQLite", "sde", [r"sqlite"]),
    ("NoSQL", "both", [r"nosql"]),
    ("Cassandra", "sde", [r"cassandra"]),
    ("DynamoDB", "sde", [r"dynamo ?db"]),
    ("Elasticsearch", "both", [r"elastic ?search", r"opensearch"]),
    ("Snowflake", "ml", [r"(?-i)Snowflake"]),
    ("BigQuery", "ml", [r"big ?query"]),
    ("Spark", "ml", [r"(?:apache )?spark", r"pyspark"]),
    ("Hadoop", "ml", [r"hadoop", r"hdfs", r"(?-i)Hive"]),
    ("Airflow", "ml", [r"airflow"]),
    ("dbt", "ml", [r"(?-i)dbt"]),
    ("ETL", "ml", [r"etl", r"elt", r"data pipelines?"]),
    ("Databricks", "ml", [r"databricks"]),
    ("Data Warehousing", "ml", [r"data ?warehous(?:e|ing)", r"redshift"]),
    ("Knowledge Graphs", "ml", [r"knowledge graphs?", r"neo4j", r"sparql", r"rdf4?j?"]),
    # ---- cloud / devops
    ("AWS", "both", [r"aws", r"amazon web services", r"ec2", r"(?-i)S3", r"lambda functions?", r"aws lambda"]),
    ("Azure", "both", [r"azure(?! openai)", r"microsoft azure"]),
    ("GCP", "both", [r"gcp", r"google cloud(?: platform)?"]),
    ("Docker", "both", [r"docker", r"containeri[sz](?:ation|ed)"]),
    ("Kubernetes", "both", [r"kubernetes", r"k8s", r"helm"]),
    ("Terraform", "sde", [r"terraform", r"infrastructure as code"]),
    ("CI/CD", "sde", [r"ci ?/ ?cd", r"continuous integration", r"jenkins", r"github actions", r"gitlab ci",
                      r"codepipeline", r"circleci"]),
    ("Linux", "both", [r"linux", r"unix"]),
    ("Git", "both", [r"git(?!hub|lab)", r"github", r"gitlab", r"version control", r"codecommit"]),
    ("Monitoring", "sde", [r"prometheus", r"grafana", r"datadog", r"observability"]),
    ("Serverless", "sde", [r"serverless"]),
    # ---- ML / AI
    ("Machine Learning", "ml", [r"machine learning", r"(?-i)ML"]),
    ("Deep Learning", "ml", [r"deep learning", r"neural networks?", r"(?-i)(?:CNNs?|RNNs?|LSTMs?)"]),
    ("PyTorch", "ml", [r"pytorch", r"torch(?:vision)?"]),
    ("TensorFlow", "ml", [r"tensorflow", r"keras"]),
    ("scikit-learn", "ml", [r"scikit-?learn", r"sklearn"]),
    ("XGBoost", "ml", [r"xgboost", r"lightgbm", r"catboost", r"gradient boosting"]),
    ("NumPy", "ml", [r"numpy"]),
    ("Pandas", "ml", [r"pandas"]),
    ("Data Visualization", "ml", [r"matplotlib", r"seaborn", r"plotly", r"tableau", r"power ?bi", r"data visuali[sz]ation"]),
    ("Statistics", "ml", [r"statistics", r"statistical", r"probability", r"hypothesis testing", r"a/b testing"]),
    ("Computer Vision", "ml", [r"computer vision", r"opencv", r"image (?:classification|segmentation|processing)",
                               r"object detection", r"yolo"]),
    ("NLP", "ml", [r"nlp", r"natural language processing", r"natural language understanding"]),
    ("Transformers", "ml", [r"transformers", r"transformer models?", r"hugging ?face", r"bert", r"attention mechanisms?"]),
    ("LLMs", "ml", [r"llms?", r"large language models?", r"gpt-?\d\w*", r"openai(?: api)?", r"azure openai", r"gemini",
                    r"claude", r"llama", r"qwen", r"mistral"]),
    ("RAG", "ml", [r"rag", r"retrieval[- ]augmented(?: generation)?"]),
    ("LangChain", "ml", [r"langchain"]),
    ("LangGraph", "ml", [r"langgraph"]),
    ("LlamaIndex", "ml", [r"llama ?index"]),
    ("Vector Databases", "ml", [r"vector (?:databases?|dbs?|stores?|search)", r"chroma(?:db)?", r"pinecone", r"weaviate",
                                r"faiss", r"milvus", r"qdrant", r"pgvector"]),
    ("Embeddings", "ml", [r"embeddings?", r"semantic search"]),
    ("Generative AI", "ml", [r"generative ai", r"gen ?ai", r"diffusion models?", r"gans?"]),
    ("AI Agents", "ml", [r"ai agents?", r"agentic", r"multi-agent", r"llm agents?", r"react agents?"]),
    ("Prompt Engineering", "ml", [r"prompt engineering", r"prompting"]),
    ("Fine-tuning", "ml", [r"fine-?tun(?:e|ing)", r"lora", r"peft", r"rlhf", r"instruction tuning"]),
    ("MLOps", "ml", [r"mlops", r"mlflow", r"kubeflow", r"model (?:deployment|serving|monitoring)", r"sagemaker",
                     r"vertex ai", r"triton", r"vllm", r"onnx", r"tensorrt"]),
    ("Model Evaluation", "ml", [r"model evaluation", r"evaluation metrics", r"llm evals?"]),
    ("CUDA", "ml", [r"cuda", r"gpu programming"]),
    ("Reinforcement Learning", "ml", [r"reinforcement learning"]),
    ("Recommender Systems", "ml", [r"recommend(?:er|ation) (?:systems?|engines?)"]),
    ("Time Series", "ml", [r"time[- ]series", r"forecasting"]),
    ("Speech", "ml", [r"speech recognition", r"\basr\b", r"text[- ]to[- ]speech", r"\btts\b"]),
    ("Jupyter", "ml", [r"jupyter"]),
    # ---- CS fundamentals / practice
    ("Data Structures & Algorithms", "sde", [r"data structures?", r"algorithms?", r"dsa"]),
    ("OOP", "sde", [r"oop", r"object[- ]oriented"]),
    ("System Design", "sde", [r"system design", r"(?:low|high)[- ]level design", r"scalable systems?", r"scalability"]),
    ("Distributed Systems", "sde", [r"distributed systems?", r"distributed computing"]),
    ("Operating Systems", "sde", [r"operating systems?"]),
    ("Computer Networks", "sde", [r"computer networks?", r"networking", r"tcp/ip"]),
    ("DBMS", "sde", [r"dbms", r"database design", r"database management"]),
    ("Concurrency", "sde", [r"multi-?threading", r"concurrency", r"concurrent programming"]),
    ("Design Patterns", "sde", [r"design patterns?"]),
    ("Testing", "sde", [r"unit test(?:s|ing)?", r"pytest", r"junit", r"test automation", r"selenium", r"playwright",
                        r"cypress", r"tdd"]),
    ("Agile", "both", [r"agile", r"scrum"]),
    ("Embedded Systems", "sde", [r"embedded systems?", r"embedded c", r"rtos", r"firmware", r"microcontrollers?"]),
    ("Unity", "sde", [r"(?-i)Unity(?:3D)?", r"unity engine"]),
    ("Security", "sde", [r"cyber ?security", r"application security", r"owasp", r"penetration testing"]),
]


def _compile(p):
    flags = re.I
    if p.startswith("(?-i)"):
        p, flags = p[5:], 0
    return re.compile(r"(?<![A-Za-z0-9])(?:" + p + r")(?![A-Za-z0-9+#])", flags)


_COMPILED = [(name, role, [_compile(p) for p in pats]) for name, role, pats in TAXONOMY]
ROLE_OF = {name: role for name, role, _ in TAXONOMY}


def find_skills(text):
    """Return {canonical skill: number of mentions} found in text."""
    hits = {}
    for name, _role, pats in _COMPILED:
        n = sum(len(p.findall(text)) for p in pats)
        if n:
            hits[name] = n
    return hits
