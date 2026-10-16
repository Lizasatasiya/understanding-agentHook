from setuptools import setup, find_packages

setup(
    name="understanding-agent",
    version="0.4.0",
    packages=find_packages(),
    package_data={"understanding_agent": ["ui/*"]},
    include_package_data=True,
    entry_points={
        "console_scripts": [
            "understanding-agent = understanding_agent.cli:main",
            "understanding-agent-ui = understanding_agent.server:run",
            "understanding-agent-sync-history = understanding_agent.sync_history:main",
            "understanding-agent-push-guard = understanding_agent.push_guard:main",
        ]
    },
    python_requires=">=3.10",
    install_requires=[],
    extras_require={
        "voice": [
            "sounddevice",
            "openai-whisper",
            "numpy",
        ],
        "ui": [
            "fastapi",
            "uvicorn",
            "pydantic>=2",
        ],
        "service": [
            # Central ledger service (service/, Pokerface P3 / Judas)
            "fastapi",
            "uvicorn",
            "sqlalchemy>=2",
            "pydantic>=2",
        ],
    },
)
