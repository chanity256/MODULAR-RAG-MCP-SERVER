# QA 用例 ↔ pytest 对照表

> **版本**：1.0（2026-11-13，AI_NATIVE_SDLC_SPEC SD3.2 / SD3.3）
> **范围**：[QA_TEST_PLAN.md](QA_TEST_PLAN.md) 1.2 的 G、H、I、J、M、N、O 七段共 66 条。A–F 随 Streamlit 退役失效，K、L 依赖 DeepSeek key 与真实 reranker，均不在范围内。
> **单一来源**：本文件只在 `.factory/skills/qa-tester/` 保留一份，不进 `.claude/` / `.github/` 镜像（`scripts/sync_skills.py` 的 `QA_*.md` 规则）。
> **维护**：新增或改写 QA 用例、或新增对应 pytest 时同步本表；节点 ID 均相对仓库根，全部可被 `pytest <节点>` 直接运行，均不需要 `RAG_*_INTEGRATION` 门控。

## 图例

| 列 | 取值 |
|----|------|
| 覆盖 | ✅ 核心预期已有离线断言；🟡 部分（核心场景有断言，个别细节缺，或只能离线验证结构）；❌ 无（核心预期没有任何 pytest 断言，只测到零件或相邻场景也算 ❌）；⚠️ 已知缺陷（预期当前不成立，以 `xfail(strict=True)` 锁定）；— 用例失效 |
| 可离线 | 是：零网络 + fake embedding / LLM 可验证核心预期；部分：结构可离线，内容需真实模型；否：依赖真实 embedding 语义排序、真实 Vision LLM 或长时间运行 |
| SD3.3 | 「补」= SD3.3 为该用例新增的 pytest |

文中缩写：`IE` = `tests/unit/test_ingest_entry.py`，`DI` = `tests/e2e/test_data_ingestion.py`，`ICS` = `tests/e2e/test_ingest_cli_subprocess.py`，`QE` = `tests/unit/test_query_entry.py`，`EE` = `tests/unit/test_evaluate_entry.py`，`GAP` = `tests/e2e/test_qa_offline_gaps.py`（SD3.3 新增）。

## G. CLI — 数据摄取

| ID | pytest | 覆盖 | 可离线 | SD3.3 | 备注 |
|----|--------|------|--------|-------|------|
| G-01 | `DI::test_cli_single_file_path`；`IE::test_main_exit_zero_all_success` | ✅ | 是 | | 用合成 PDF，断言 `1 total \| 1 ingested`、exit 0、store 有记录 |
| G-02 | `DI::test_cli_directory_ingest_end_to_end_artifacts`；`IE::test_collect_files_directory_walk_sorted_and_filtered` | ✅ | 是 | | 汇总行与 Chroma / BM25 / 图片产物 |
| G-03 | `DI::test_cli_directory_ingest_end_to_end_artifacts`；`IE::test_run_ingest_relays_collection_force_and_progress` | ✅ | 是 | | 命中 metadata 的 collection 正确；Web UI 可见性属 Web UI 范围 |
| G-04 | `ICS::test_usage_errors_exit_2`；`ICS::test_sync_dry_run_fresh_install_prints_plan_and_writes_nothing`；`ICS::test_dry_run_against_existing_stores_classifies_and_leaves_bytes_unchanged` | ✅ | 是 | | 计划 1.2 订正：`--dry-run` 须配 `--sync` / `--prune` |
| G-05 | `IE::test_main_force_reingests_skipped_file`；`DI::test_cli_rerun_skips_unchanged_and_force_reingests` | ✅ | 是 | | 向量数不增长 |
| G-06 | `DI::test_cli_rerun_skips_unchanged_and_force_reingests`；`IE::test_main_exit_zero_all_skipped` | ✅ | 是 | | |
| G-07 | `IE::test_run_ingest_verbose_prints_progress_lines`；`IE::test_run_ingest_quiet_by_default` | ✅ | 是 | | 计划 1.2 订正：输出阶段进度行，不是 DEBUG 日志 |
| G-08 | `IE::test_main_exit_two_on_config_error`；`ICS::test_dry_run_against_existing_stores_classifies_and_leaves_bytes_unchanged` | 🟡 | 是 | | 缺：完整摄取证明 `--config` 中的值生效（dry-run 已证明 `persist_path` 生效） |
| G-09 | `IE::test_main_exit_two_missing_path`；`IE::test_collect_files_missing_path_raises` | ✅ | 是 | | 计划 1.2 订正：exit 2 + "does not exist" |
| G-10 | `IE::test_collect_files_directory_walk_sorted_and_filtered`；`DI::test_cli_directory_ingest_end_to_end_artifacts` | ✅ | 是 | | 计划 1.2 订正：`.txt` 在收集阶段被过滤，不计入汇总 |
| G-11 | `tests/unit/test_image_captioner_fallback.py::test_enabled_writes_captions_into_metadata`；`tests/unit/test_image_captioner_fallback.py::test_enabled_calls_vision_llm_with_prompt_and_path` | 🟡 | 部分 | | caption 写入路径已用 mock vision 测；CLI 级与真实 caption 内容未测 |

## H. CLI — 查询

| ID | pytest | 覆盖 | 可离线 | SD3.3 | 备注 |
|----|--------|------|--------|-------|------|
| H-01 | `QE::test_run_query_renders_topk_results`；`QE::test_main_real_hybrid_stack_offline` | 🟡 | 否 | | 输出格式全断言；「Top 结果来自 complex_technical_doc」依赖真实 embedding 排序 |
| H-02 | `QE::test_run_query_topk_passthrough`；`tests/integration/test_hybrid_search.py::test_hybrid_topk_limit_respected` | 🟡 | 部分 | | 条数上限可离线；命中特定文档依赖语义 |
| H-03 | `QE::test_run_query_collection_filter_passed`；`tests/integration/test_hybrid_search.py::test_filters_collection_narrows_results`；`GAP::TestLifecycle::test_n03_collections_are_isolated` | 🟡 | 是 | | 集合过滤已在真实栈断言；未经 CLI 端到端 |
| H-04 | `QE::test_run_query_verbose_sections` | ✅ | 是 | | |
| H-05 | `QE::test_run_query_verbose_no_rerank_shows_skipped`；`QE::test_main_no_rerank_skips_reranker_construction` | ✅ | 是 | | verbose 仍打印 `--- rerank (skipped) ---` 段头 |
| H-06 | `QE::test_main_invalid_query_returns_usage`；`tests/unit/test_query_processor.py::test_empty_or_whitespace_query_raises` | 🟡 | 是 | | CLI 用例经 fake 抛错；真实栈空查询未经 CLI |
| H-07 | `GAP::test_h07_long_query_cli_completes` | ✅ | 是 | 补 | 约 250 字符中英混合查询经 `query.py` 真实离线栈返回结果 |
| H-08 | `tests/e2e/test_markdown_vault_query.py::test_query_cli_hits_note_by_keyword`（类比） | 🟡 | 否 | | Top-3 排序依赖真实 embedding |
| H-09 | `QE::test_main_no_results_is_ok_with_hint` | 🟡 | 否 | | 「分数较低」是语义判断；只锁定不崩溃与无命中提示 |
| H-10 | `QE::test_main_persists_query_trace_readable_by_trace_service` | ✅ | 是 | 补 | 经 `TraceService.latest("query")` 读回本次 query 文本与时间 |

## I. CLI — 评估

| ID | pytest | 覆盖 | 可离线 | SD3.3 | 备注 |
|----|--------|------|--------|-------|------|
| I-01 | `EE::TestMain::test_ok_exit_0_and_renders_metrics`；`tests/e2e/test_recall.py::test_recall_hits_threshold` | 🟡 | 部分 | | 结构可离线；CLI 用例未断言 MRR 字段；指标数值依赖真实数据 |
| I-02 | `EE::TestDefaults::test_default_test_set_resolves_from_settings`；`EE::TestMain::test_ok_exit_0_and_renders_metrics` | 🟡 | 部分 | | 仓库 golden set 只校验存在，未经 CLI 跑一遍 |
| I-03 | — | — | — | | 计划 1.2 标失效：`evaluate.py` 无 `--json` |
| I-04 | — | — | — | | 计划 1.2 标失效：`evaluate.py` 无 `--no-search` |
| I-05 | `EE::TestMain::test_missing_test_set_exit_2` | ✅ | 是 | | exit 2 + "not found" |

## J. MCP Server 协议交互

| ID | pytest | 覆盖 | 可离线 | SD3.3 | 备注 |
|----|--------|------|--------|-------|------|
| J-01 | `tests/integration/test_mcp_server.py::test_initialize_handshake`；`tests/integration/test_mcp_server.py::test_stderr_has_logs_stdout_clean_on_eof` | 🟡 | 部分 | | 子进程启动 `mcp_server.server` 握手；`main.py` 入口与 VS Code 连接未测 |
| J-02 | `tests/integration/test_mcp_server.py::test_tools_list_and_unknown_method`；`tests/e2e/test_mcp_client.py::test_mcp_client_full_walkthrough`；`tests/unit/test_agentic_tool.py::TestRegistration::test_disabled_not_registered` | ✅ | 是 | | 3 个工具；`agentic_search` 仅启用时注册 |
| J-03 | `tests/e2e/test_mcp_client.py::test_mcp_client_full_walkthrough`；`tests/unit/test_query_knowledge_hub_tool.py::TestExecution::test_returns_markdown_content_and_citations` | 🟡 | 部分 | | 结构与引用可离线；答案含混合检索关键词依赖真实数据 |
| J-04 | `tests/unit/test_list_collections.py::TestListCollections::test_returns_names_and_chunk_counts`；`tests/e2e/test_mcp_client.py::test_mcp_client_full_walkthrough` | ✅ | 是 | | |
| J-05 | `tests/unit/test_get_document_summary.py::TestGetDocumentSummary::test_returns_structured_summary`；`tests/unit/test_get_document_summary.py::TestGetDocumentSummary::test_registration_and_call_via_protocol_handler` | 🟡 | 是 | | fake reader；未对真实 simple.pdf 端到端 |
| J-06 | `tests/integration/test_mcp_server.py::test_query_knowledge_hub_multimodal_image_return`；`tests/unit/test_multimodal_assembler.py::TestMultimodalAssembler::test_appends_image_content_after_markdown` | 🟡 | 部分 | | Text + Base64 Image 结构已测；caption 内容需真实 Vision LLM |
| J-07 | `tests/integration/test_hybrid_search.py::test_filters_exclude_all_returns_empty`；`tests/unit/test_query_knowledge_hub_tool.py::TestExecution::test_empty_results_friendly_message` | 🟡 | 是 | | 两半分别测；未经 MCP 串起来 |
| J-08 | `tests/integration/test_mcp_server.py::test_query_knowledge_hub_call`；`tests/unit/test_query_knowledge_hub_tool.py::TestValidation::test_invalid_query_raises_invalid_params` | ✅ | 是 | | `-32602` |
| J-09 | — | ❌ | 否 | | 30 分钟稳定性 soak，不进离线套件 |
| J-10 | `tests/unit/test_response_builder.py::TestResponseBuilder::test_structured_content_citation_fields`；`tests/e2e/test_mcp_client.py::test_mcp_client_full_walkthrough` | 🟡 | 是 | | 计划 1.2 订正字段名为 `source`、分数不保证 0–1 |

## M. 配置变更与容错

| ID | pytest | 覆盖 | 可离线 | SD3.3 | 备注 |
|----|--------|------|--------|-------|------|
| M-01 | `tests/unit/test_llm_providers_smoke.py::test_api_error_response_raises_readable`；`tests/unit/test_reranker_fallback.py::test_backend_exception_returns_input_order_with_fallback_flag` | 🟡 | 是 | | 计划 1.2 订正：LLM 只在 `rerank.backend=llm` 时被调用，失败回退、exit 0；未经 CLI 串起来 |
| M-02 | `tests/unit/test_embedding_providers_smoke.py::test_api_error_response_raises_readable`；`tests/unit/test_ingestion_retry.py::test_permanent_error_is_not_retried` | 🟡 | 是 | | 未经 `ingest.py` 断言报错指向 embed 阶段 |
| M-03 | `tests/integration/test_hybrid_search.py::test_dense_failure_degrades_to_sparse` | 🟡 | 是 | | 计划 1.2 订正同 M-01 |
| M-04 | `tests/unit/test_ingestion_pipeline.py::test_llm_disabled_degradation_markers_still_ingest`；`tests/unit/test_image_captioner_fallback.py::test_disabled_keeps_refs_and_marks_unprocessed` | ✅ | 是 | | Web UI `captioned=0` 展示不在 pytest 范围 |
| M-05 | `QE::test_main_yaml_syntax_error_returns_usage` | ✅ | 是 | 补 | 此前只测了配置文件缺失 |
| M-06 | `tests/unit/test_config_loading.py::test_missing_section_raises`；`tests/unit/test_config_loading.py::test_missing_embedding_provider_raises_with_path` | 🟡 | 是 | | 未经 `ingest.py` 断言 exit 2 |
| M-07 | `tests/integration/test_chroma_store_roundtrip.py::test_persistence_across_instances` | 🟡 | 是 | | 目录自动创建只是隐含成立；计划 1.2 订正键名为 `persist_path` |
| M-08 | `tests/unit/test_daily_traces.py::TestTraceServiceDirectoryMode::test_removed_file_drops_out_of_cache`；`tests/integration/test_api_traces.py::TestParseCache::test_deleted_file_returns_empty` | ✅ | 是 | | 后端空状态；前端文案不在 pytest 范围 |
| M-09 | `tests/unit/test_dashboard_services.py::test_trace_service_filters_and_searches` | 🟡 | 是 | | 只测单文件模式的损坏行跳过，按日目录模式未测 |
| M-10 | `tests/unit/test_document_chunker.py::test_config_driven_chunk_size_changes_chunk_count`；`tests/unit/test_recursive_splitter_lib.py::test_overlap_zero_vs_positive_adjacent_chunks` | ✅ | 是 | 补 | 新用例补「每个 chunk ≤ chunk_size」；计划 1.2 订正键名为 `splitter.chunk_size` |
| M-11 | `tests/unit/test_recursive_splitter_lib.py::test_overlap_zero_vs_positive_adjacent_chunks` | ✅ | 是 | 补 | overlap=0 相邻无重复词，>0 时有 |
| M-12 | `tests/unit/test_chunk_refiner.py::test_use_llm_false_never_calls_llm`；`tests/unit/test_chunk_refiner.py::test_rule_based_scenario` | ✅ | 是 | | 计划 1.2 订正开关为 `use_llm: false` |
| M-13 | `tests/unit/test_metadata_enricher_contract.py::test_use_llm_false_never_calls_llm`；`tests/unit/test_metadata_enricher_contract.py::test_rule_mode_fills_nonempty_title_summary_tags` | ✅ | 是 | | 规则模式 title/summary/tags 非空 |
| M-14 | `QE::test_run_query_verbose_sections` | 🟡 | 是 | | 缺：`retrieval.top_k_dense` 透传给 Dense 路的断言 |
| M-15 | `tests/unit/test_fusion_rrf.py::test_k_reads_from_settings_override`；`tests/unit/test_fusion_rrf.py::test_yaml_rrf_k_is_config_driven` | ✅ | 是 | | |

## N. 数据生命周期闭环

| ID | pytest | 覆盖 | 可离线 | SD3.3 | 备注 |
|----|--------|------|--------|-------|------|
| N-01 | `GAP::TestLifecycle::test_n01_ingest_query_delete_query`；`tests/unit/test_document_manager.py::test_delete_document_coordinates_four_stores` | ✅ | 是 | 补 | 真实 Chroma + BM25 + HybridSearch，删除走 `DataService` |
| N-02 | `GAP::TestLifecycle::test_n02_reingest_after_delete_is_not_skipped` | ✅ | 是 | 补 | |
| N-03 | `GAP::TestLifecycle::test_n03_collections_are_isolated` | ✅ | 是 | 补 | |
| N-04 | `GAP::TestLifecycle::test_n04_clear_all_then_recover` | ✅ | 是 | 补 | 清空 = 删除全部存储目录，与 `qa_bootstrap.py clear` 等价；脚本本身不在 `tests/` 范围 |
| N-05 | `GAP::TestLifecycle::test_n05_same_file_in_two_collections_both_searchable`（xfail strict） | ⚠️ | 是 | 补 | chunk_id 不含 collection，第二次摄取覆盖第一个集合（[04_INGESTION_ROBUSTNESS_SPEC_frozen.md](../../../docs/frozen/04_INGESTION_ROBUSTNESS_SPEC_frozen.md)「已知遗留问题」）；修复后 strict xfail 会转为失败，提示同步本表 |
| N-06 | `GAP::TestLifecycle::test_n06_delete_in_one_collection_keeps_the_other`；`tests/unit/test_document_manager.py::test_delete_document_keeps_other_collections_history_and_shared_hash` | ✅ | 是 | 补 | 受 N-05 影响，只锁定 col_2 不受删除波及 |

## O. 文档替换与多场景验证

| ID | pytest | 覆盖 | 可离线 | SD3.3 | 备注 |
|----|--------|------|--------|-------|------|
| O-01 | `GAP::TestDocuments::test_o01_chinese_technical_doc_sparse_recall`；`tests/unit/test_sparse_encoder.py::test_cjk_char_by_char` | ✅ | 是 | 补 | 计划 1.2 订正：CJK 逐字分词，项目不使用 jieba |
| O-02 | `GAP::TestDocuments::test_o02_table_content_is_searchable` | ✅ | 是 | 补 | |
| O-03 | `tests/unit/test_loader_pdf_contract.py::test_load_pdf_with_images_inserts_placeholder`；`tests/unit/test_loader_pdf_contract.py::test_image_file_saved_under_doc_hash_dir` | 🟡 | 否 | | 图片抽取可离线；按 caption 内容召回需真实 Vision LLM |
| O-04 | `GAP::TestDocuments::test_o04_long_document_front_and_back_recall` | ✅ | 是 | 补 | 计划 1.2 订正：chunk 无逐页 `page`，断言文档级 `page_count` ≥ 30 |
| O-05 | `GAP::TestDocuments::test_o05_technical_terms_survive_chunking` | ✅ | 是 | 补 | |
| O-06 | `tests/e2e/test_markdown_vault_query.py::test_query_cli_hits_note_by_keyword`；`tests/e2e/test_markdown_vault_query.py::test_mcp_query_knowledge_hub_cites_note_path` | 🟡 | 是 | | Markdown 摄取 + 关键词查询已测；不针对 DEV_SPEC 本身（文档会变，断言易碎） |
| O-07 | `tests/e2e/test_recall.py::test_recall_hits_threshold` | 🟡 | 否 | | 中英文档评估分数对比依赖真实 embedding 语义 |
| O-08 | `DI::test_cli_directory_ingest_end_to_end_artifacts`；`DI::test_cli_broken_pdf_maps_to_failed_file_not_crash` | 🟡 | 是 | | 用合成 PDF；真实 `sample_documents/` 目录含未入库的本地 PDF，不进测试 |
| O-09 | `GAP::TestDocuments::test_o09_short_non_technical_doc` | ✅ | 是 | 补 | |

## 汇总

| 段 | 用例 | ✅ | 🟡 | ❌ | ⚠️ | — |
|----|------|----|----|----|----|----|
| G | 11 | 9 | 2 | 0 | 0 | 0 |
| H | 10 | 4 | 6 | 0 | 0 | 0 |
| I | 5 | 1 | 2 | 0 | 0 | 2 |
| J | 10 | 3 | 6 | 1 | 0 | 0 |
| M | 15 | 8 | 7 | 0 | 0 | 0 |
| N | 6 | 5 | 0 | 0 | 1 | 0 |
| O | 9 | 5 | 4 | 0 | 0 | 0 |
| 合计 | 66 | 35 | 27 | 1 | 1 | 2 |

**SD3.3 补测范围**：SD3.2 对照时「❌ 且可离线」的用例共 15 条（H-07、H-10、M-05、M-11、N-01–N-06、O-01、O-02、O-04、O-05、O-09），现已全部有 pytest，其中 N-05 是已知缺陷，以 strict xfail 锁定；M-10 原为 🟡，由 M-11 的新用例顺带补齐「chunk ≤ chunk_size」断言。剩余唯一的 ❌ 是 J-09（30 分钟 soak，不可离线）。

**🟡 不在 SD3.3 范围**：规格 §3.3 只要求补「能离线且无对应 pytest」的用例。表中 🟡 的「缺」列可作后续补测候选，优先级较高的是 G-08（`--config` 生效）、M-09（按日目录模式的损坏行）、M-14（`top_k_dense` 透传）。

**qa-tester 回归选择**：SD4.3 的定时 QA 回归只跑「可离线 = 是」的用例；「否 / 部分」的用例仍需人工或门控环境执行。
