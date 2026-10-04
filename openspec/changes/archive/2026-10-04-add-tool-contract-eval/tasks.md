# Tasks

## 1. 项目骨架与依赖

- [x] 1.1 创建 `pyproject.toml`、`src/agenteval/`、`tests/`、`examples/` 骨架，验证 `python -c "import agenteval"` 在可编辑安装后成功
- [x] 1.2 声明运行时与开发依赖（pydantic、pyyaml、jsonschema、pytest），验证 `python -m pytest --version` 与 `python -c "import pydantic, yaml, jsonschema"` 均可执行
- [x] 1.3 编写 `README.md` 骨架，包含安装与最简运行方式，验证文档中的安装命令可原样执行

## 2. 核心数据模型与运行存储

- [x] 2.1 实现 Case / Trace / Verdict / Run 模型，验证 `tests/test_models.py` 覆盖字段校验、非法输入报错与序列化往返
- [x] 2.2 实现判定结果汇总逻辑（pass/fail/error 计数），验证汇总测试通过
- [x] 2.3 实现 JSONL 加 summary 的运行存储，验证 `tests/test_store.py` 覆盖自动建目录、写入与重新读取
- [x] 2.4 实现运行目录解析（默认 `.agenteval/runs/`，支持 `AGENTEVAL_HOME` 覆盖），验证环境变量覆盖场景测试通过

## 3. 工具契约断言引擎

- [x] 3.1 实现工具适配器协议与结构化工具错误类型，验证协议与错误类型单元测试通过
- [x] 3.2 实现故障注入桩件（慢工具、限流工具、幂等工具、部分失败工具），验证 `tests/test_fakes.py` 覆盖各桩件行为
- [x] 3.3 实现参数校验契约断言（必填缺失、类型错误），验证两种失败模式测试通过
- [x] 3.4 实现超时契约断言，验证超时用例在阈值内返回 error 且不悬挂
- [x] 3.5 实现限流与重试契约断言，验证退避成功与超过上限两条路径测试通过
- [x] 3.6 实现幂等重试契约断言，验证重复调用后副作用计数为 1
- [x] 3.7 实现部分失败回滚契约断言，验证回滚后净副作用为空

## 4. 运行器、CLI 与 pytest 集成

- [x] 4.1 实现契约用例运行器与用例文件载入（JSON/YAML），验证批量执行后产出完整 Run 记录
- [x] 4.2 实现 CLI `run` / `list` / `show` 子命令，验证 `python -m agenteval show <run_id>` 能读取真实运行结果
- [x] 4.3 实现 pytest 集成入口与示例注册表，验证 `python -m pytest tests/test_contract_e2e.py` 通过
- [x] 4.4 在 `README.md` 补齐用例编写、运行与结果查看说明，验证文档中的命令逐条可执行

## 5. 端到端验证

- [x] 5.1 用示例工具与六类契约用例跑一次完整运行，验证产出 6 条 pass 判定且结果可由 CLI 回读
- [x] 5.2 运行 `openspec validate add-tool-contract-eval --strict` 并确认通过
