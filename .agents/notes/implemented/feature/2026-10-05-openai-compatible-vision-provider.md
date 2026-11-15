# Agent Note: OpenAI 兼容 Vision provider（继承 Azure 实现）

Status: implemented

## Problem

G14 的 Vision LLM 此前只有 Azure 实现，而实际使用的视觉端点是智谱 GLM（OpenAI 兼容协议，`POST {base_url}/chat/completions` + Bearer）。需求是让任何 OpenAI 兼容视觉端点（GLM、DashScope 兼容模式、本地网关）经配置可用，且不重复实现图片预处理、payload 构建和响应解析。

## Decision

`OpenAICompatibleVisionLLM` 继承 `AzureVisionLLM`，仅覆盖两处 Azure 特有逻辑：`_build_url` 改为 `{base_url}/chat/completions`，`_build_headers` 改为可选 Bearer 认证（`api_key` 可选，本地网关允许匿名）。图片加载/压缩/payload/响应解析全部复用父类。注册名为 `openai`，经 `VisionLLMConfig.base_url` / `model` 区分具体服务（`base_url` 必填，与 `EmbeddingConfig` 同款配置方式）；默认 transport 复用 Azure 的 urllib 实现仅替换错误标签。settings.yaml 已切到 `glm-5.3-flash` + `${ZAI_API_KEY}`。

## Alternatives considered

- **独立实现类（复制 Azure 逻辑）**：最强论据是与 Azure 解耦，父类改动不会波及本实现。被否：图片预处理/压缩、Vision payload 构建、响应解析约几百行纯复制，两份实现必然漂移；且两者协议请求体本就一致，唯一差异就是 URL 和认证头。
- **把 AzureVisionLLM 泛化成带 URL/auth 策略参数的基类**：最强论据是不用继承去"覆盖"父类方法，组合更干净。被否：当前只有两种真实协议形态（Azure 定制 URL vs OpenAI 标准 URL），引入策略抽象是过度设计；继承让差异一目了然。
- **复用 `OpenAICompatibleLLM`（文本版）扩展多模态**：最强论据是 provider 数量不增。被否：文本实现没有图片加载/预处理链路，多模态消息体结构也不同，改造它等于重写。

## Consequences

- 收益：一个实现覆盖全部 OpenAI 兼容视觉端点，新增服务商只是改配置；Azure 实现的图片链路修复自动生效。
- 代价：本实现耦合在 `AzureVisionLLM` 的非公开方法（`_build_url` / `_build_headers`）签名上，父类重构这两处时必须同步检查子类（`openai_llm.py` 的文本版是独立实现，无此耦合）。
- 代价：provider 名为 `openai` 但行为取决于 `base_url` 指向的服务——错误信息标签统一为 `openai-vision`，排查问题时需先确认实际端点。

## Verification

- 14 个 mock-HTTP 单测覆盖 URL 拼接、Bearer 头、匿名调用、payload 与响应解析。
- 真图验收通过：tabby cat 正确识别，621 tokens（素材见 `7508f55`）。
- 全量测试 1058 passed / 2 skipped。
