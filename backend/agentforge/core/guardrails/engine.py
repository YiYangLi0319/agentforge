"""护栏引擎：统一编排输入检查（注入/审核）与输出检查（PII 脱敏），产出结构化裁决。"""

from enum import StrEnum

from pydantic import BaseModel, Field

from agentforge.core.guardrails.injection import score_injection
from agentforge.core.guardrails.moderation import moderate
from agentforge.core.guardrails.pii import mask_pii


class GuardrailVerdict(StrEnum):
    ALLOW = "allow"
    BLOCK = "block"


class GuardrailResult(BaseModel):
    verdict: GuardrailVerdict = GuardrailVerdict.ALLOW
    stage: str = ""  # input | output
    categories: list[str] = Field(default_factory=list)
    injection_score: float = 0.0
    reasons: list[str] = Field(default_factory=list)
    text: str = ""  # 处理后的文本（输出阶段可能被脱敏）
    pii_types: list[str] = Field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return self.verdict == GuardrailVerdict.BLOCK


class GuardrailsEngine:
    def __init__(
        self,
        *,
        enabled: bool = True,
        block_injection: bool = True,
        mask_output_pii: bool = True,
        moderation: bool = True,
        injection_threshold: float = 0.6,
    ):
        self.enabled = enabled
        self.block_injection = block_injection
        self.mask_output_pii = mask_output_pii
        self.moderation = moderation
        self.injection_threshold = injection_threshold

    def check_input(self, text: str) -> GuardrailResult:
        """输入护栏：注入检测 + 内容审核。命中即 BLOCK。"""
        result = GuardrailResult(stage="input", text=text)
        if not self.enabled:
            return result

        score, reasons = score_injection(text)
        result.injection_score = round(score, 2)
        if self.block_injection and score >= self.injection_threshold:
            result.verdict = GuardrailVerdict.BLOCK
            result.categories.append("prompt_injection")
            result.reasons.extend(reasons)

        if self.moderation:
            cats = moderate(text)
            if cats:
                result.verdict = GuardrailVerdict.BLOCK
                result.categories.extend(cats)
                result.reasons.append("命中内容审核类别: " + ", ".join(cats))

        if result.blocked:
            try:
                from agentforge.observability.metrics import record_guardrail_block

                for cat in result.categories:
                    record_guardrail_block(cat)
            except Exception:  # noqa: BLE001
                pass
        return result

    def check_output(self, text: str) -> GuardrailResult:
        """输出护栏：PII 脱敏（不拦截，只清洗）。"""
        result = GuardrailResult(stage="output", text=text)
        if not self.enabled or not self.mask_output_pii:
            return result
        masked, pii_types = mask_pii(text)
        result.text = masked
        result.pii_types = pii_types
        return result

    @staticmethod
    def refusal_message(result: GuardrailResult) -> str:
        if "prompt_injection" in result.categories:
            return "抱歉，你的请求似乎试图绕过我的安全设定，我无法执行。如果是正常问题，请换一种方式描述。"
        return "抱歉，你的请求包含不适宜的内容（" + "、".join(result.categories) + "），我无法提供帮助。"
