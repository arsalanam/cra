"""
sample_questions.py
────────────────────────────────────────────────────────────────────────────
GAIA-inspired benchmark questions curated for this tutorial.

Each question demonstrates a different ReACT pattern:
  - Which tools get used
  - How many Thought→Action→Observation cycles are needed
  - What reasoning depth is required

GAIA (General AI Assistants benchmark) tests agents on real-world tasks that
require multi-step reasoning, tool use, and information synthesis.
Source: https://huggingface.co/datasets/gaia-benchmark/GAIA
"""

SAMPLE_QUESTIONS = [
    {
        "id": 1,
        "title": "Orbital Mechanics",
        "difficulty": "Level 1",
        "tags": ["math", "calculator"],
        "tools_hint": ["calculator"],
        "question": (
            "The International Space Station orbits Earth at an altitude of "
            "approximately 408 km. Given that Earth's radius is 6,371 km, and "
            "the ISS completes one full orbit every 92 minutes, calculate the "
            "ISS's approximate orbital speed in km/h. Round to the nearest integer."
        ),
        "expected_answer": "27,580 km/h (approximately)",
        "react_steps": 2,
        "explanation": (
            "Pure math — the agent uses the calculator tool to compute "
            "circumference (2π × (6371+408)) then converts minutes to hours."
        ),
    },
    {
        "id": 2,
        "title": "Vatican City Geometry",
        "difficulty": "Level 1",
        "tags": ["wikipedia", "math"],
        "tools_hint": ["wikipedia", "calculator"],
        "question": (
            "Look up Vatican City's area on Wikipedia. "
            "If Vatican City were reshaped into a perfect square, "
            "what would be the length of each side in meters? "
            "Round to the nearest meter."
        ),
        "expected_answer": "~986 meters (Vatican City is 0.44 km²)",
        "react_steps": 3,
        "explanation": (
            "Two-tool chain: Wikipedia lookup for area → calculator for "
            "sqrt(area_in_m²). Classic multi-step reasoning."
        ),
    },
    {
        "id": 3,
        "title": "Nobel Prize Research",
        "difficulty": "Level 1",
        "tags": ["web_search"],
        "tools_hint": ["web_search"],
        "question": (
            "Who were the laureates of the 2024 Nobel Prize in Physics, "
            "what institution(s) are they affiliated with, "
            "and what was the specific contribution they were awarded for?"
        ),
        "expected_answer": "Hopfield & Hinton for foundational discoveries enabling machine learning",
        "react_steps": 2,
        "explanation": (
            "Single web search, but requires synthesizing multiple facts "
            "from the result: names, institutions, and contribution."
        ),
    },
    {
        "id": 4,
        "title": "Norway GDP Analysis",
        "difficulty": "Level 2",
        "tags": ["web_search", "math"],
        "tools_hint": ["web_search", "calculator"],
        "question": (
            "Find Norway's most recent GDP per capita (USD) and the current "
            "population of Oslo. Assuming Oslo holds exactly 25% of Norway's "
            "population and all residents earn the national GDP per capita, "
            "what is Oslo's estimated total economic output in USD? "
            "Express in billions, rounded to one decimal place."
        ),
        "expected_answer": "~$215B (varies by year)",
        "react_steps": 4,
        "explanation": (
            "Multi-hop: two separate web searches (GDP, Oslo population) "
            "followed by a multi-step calculation. Tests information synthesis."
        ),
    },
    {
        "id": 5,
        "title": "Fibonacci Perfect Squares",
        "difficulty": "Level 2",
        "tags": ["python", "math"],
        "tools_hint": ["python_repl"],
        "question": (
            "From the Fibonacci sequence up to and including F(20) — "
            "where F(1)=1, F(2)=1, F(3)=2, ... — identify all values "
            "that are also perfect squares. What is their sum and count?"
        ),
        "expected_answer": "Sum=144+1+1=146 (values: 1, 1, 144); count=3",
        "react_steps": 2,
        "explanation": (
            "Python REPL shines here — generating the sequence and testing "
            "perfect squares is cleaner in code than symbolic reasoning."
        ),
    },
    {
        "id": 6,
        "title": "Wikipedia Chain Reasoning",
        "difficulty": "Level 2",
        "tags": ["wikipedia", "web_search"],
        "tools_hint": ["wikipedia", "web_search"],
        "question": (
            "According to Wikipedia, in what year was Python (programming language) "
            "first publicly released? Then find who currently leads the Python "
            "Steering Council. How many years has Python existed as of 2025?"
        ),
        "expected_answer": "1991; ~34 years; current SC varies",
        "react_steps": 4,
        "explanation": (
            "Wikipedia for historical facts, web search for current state — "
            "demonstrates when to use which tool and combining results."
        ),
    },
]
