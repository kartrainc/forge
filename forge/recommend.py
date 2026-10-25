"""Deterministic workload recommendations constrained by the live registry."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Language = Literal["python", "node", "rust", "go"]


class ServiceRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    workload: Literal["processing", "ai", "notifications", "crud", "llm_api"]
    language: Language | None = None
    cpu_intensive: bool = False
    memory_mb: int | None = Field(default=None, gt=0)
    latency_ms: int | None = Field(default=None, gt=0)
    required_library_languages: list[Language] = Field(default_factory=list)
    options: dict[str, Any] = Field(default_factory=dict)
    features: list[str] = Field(default_factory=lambda: ["items"])


class RecommendationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_name: str = "platform"
    existing_stack: Language | None = None
    team_languages: list[Language] = Field(default_factory=list)
    services: list[ServiceRequirements] = Field(min_length=1)


def recommend(request: RecommendationRequest) -> dict:
    from forge import feature_loader
    from forge.capability_resolver import resolve
    from forge.config import BackendConfig, BackendLanguage, ProjectConfig, validate_slug

    feature_loader.load_all()
    validate_slug(request.project_name)
    names = [s.name for s in request.services]
    if len(names) != len(set(names)):
        raise ValueError("Service names must be unique")
    decisions, configs = [], []
    for index, service in enumerate(request.services):
        validate_slug(service.name)
        scores = dict.fromkeys(("python", "node", "rust", "go"), 0)
        reasons: dict[str, list[str]] = {key: [] for key in scores}
        preferred = {
            "processing": "rust",
            "ai": "python",
            "notifications": "node",
            "crud": request.existing_stack or "python",
            "llm_api": request.existing_stack or "python",
        }[service.workload]
        scores[preferred] += 4
        reasons[preferred].append(f"Default fit for {service.workload}")
        if service.cpu_intensive:
            scores["rust"] += 3
            reasons["rust"].append("Native CPU parallelism; benchmark the workload")
        for language in scores:
            if language == request.existing_stack:
                scores[language] += 3
                reasons[language].append("Reuses existing runtime and operations")
            if language in request.team_languages:
                scores[language] += 2
                reasons[language].append("Matches team experience")
        alternatives, rejected = [], {}
        port = 5000 + index
        for language in scores:
            try:
                if (
                    service.required_library_languages
                    and language not in service.required_library_languages
                ):
                    raise ValueError("Required library unavailable")
                config = ProjectConfig(
                    project_name=request.project_name,
                    backends=[
                        BackendConfig(
                            name=service.name,
                            project_name=request.project_name,
                            language=BackendLanguage(language),
                            server_port=port,
                            features=service.features,
                        )
                    ],
                    frontends=[],
                    options=service.options,
                )
                config.validate()
                resolve(config)
            except (ValueError, RuntimeError) as exc:
                rejected[language] = str(exc)
                continue
            alternatives.append(
                {"language": language, "score": scores[language], "reasons": reasons[language]}
            )
        if service.language:
            if service.language in rejected:
                raise ValueError(
                    f"{service.name}: explicit {service.language} choice incompatible: {rejected[service.language]}"
                )
            selected = service.language
        elif alternatives:
            selected = sorted(alternatives, key=lambda item: (-item["score"], item["language"]))[0][
                "language"
            ]
        else:
            raise ValueError(f"{service.name}: no supported backend: {rejected}")
        assumptions = []
        if service.memory_mb or service.latency_ms:
            assumptions.append(
                "Memory/latency targets require benchmarks; language choice does not guarantee them"
            )
        if service.workload == "notifications":
            assumptions.append(
                "Use durable queues, idempotency, bounded retries, rate limits and dead-letter handling"
            )
        if service.workload == "llm_api":
            assumptions.append(
                "Remote LLM calls are I/O-bound; Python is not required solely to call an API"
            )
        configs.append(
            {
                "project_name": f"{request.project_name}-{service.name}",
                "backends": [
                    {
                        "name": service.name,
                        "language": selected,
                        "server_port": port,
                        "features": service.features,
                    }
                ],
                "frontend": {"framework": "none"},
                "options": service.options,
            }
        )
        decisions.append(
            {
                "service": service.name,
                "language": selected,
                "explicit": service.language is not None,
                "reasons": reasons[selected],
                "alternatives": sorted(
                    alternatives, key=lambda item: (-item["score"], item["language"])
                ),
                "rejected": rejected,
                "assumptions": assumptions,
            }
        )
    common = request.services[0].options
    combined = None
    if all(service.options == common for service in request.services):
        combined = {
            "project_name": request.project_name,
            "backends": [c["backends"][0] for c in configs],
            "frontend": {"framework": "none"},
            "options": common,
        }
    return {
        "policy_version": 1,
        "decisions": decisions,
        "config": combined,
        "service_configs": configs,
        "configuration_note": "Options are project-wide. Distinct requirements receive separate service configurations.",
    }
