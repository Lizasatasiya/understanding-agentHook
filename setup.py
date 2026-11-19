from setuptools import setup, find_packages

setup(
    name="understanding-agent",
    version="0.4.0",
    packages=find_packages(),
    package_data={"understanding_agent": ["ui/*"]},
    include_package_data=True,
    entry_points={
        "console_scripts": [
            "understanding-agent = understanding_agent.core.cli:main",
            "understanding-agent-ui = understanding_agent.ui.server:run",
            "understanding-agent-push-guard = understanding_agent.core.push_guard:main",
        ]
    },
    python_requires=">=3.10",
    install_requires=[
        "openai>=1.40",
    ],
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
            # Central ledger service (service/)
            "fastapi",
            "uvicorn",
            "sqlalchemy>=2",
            "pydantic>=2",
        ],
    },
)
