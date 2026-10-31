import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from support import FakeEvent

# support isolates runtime data before SDK imports.
# isort: split
from astrbot.core.agent.hooks import BaseAgentRunHooks
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.agent.tool import ToolSet
from astrbot.core.provider.entities import LLMResponse, ProviderRequest


class AgentRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_schema_initializes_text_only_requests(self):
        # 4.29.3 returns early from reset in skills_like mode with no tools,
        # skipping both request messages and stats. Full mode is the deployment workaround.
        for streaming in (False, True):
            for tools in (None, ToolSet()):
                with self.subTest(streaming=streaming, tools=tools):
                    runner = ToolLoopAgentRunner()
                    context = ContextWrapper(context=SimpleNamespace(event=FakeEvent("测试")))
                    await runner.reset(
                        provider=Mock(provider_config={"id": "test"}),
                        request=ProviderRequest(prompt="测试", func_tool=tools),
                        run_context=context,
                        tool_executor=Mock(),
                        agent_hooks=BaseAgentRunHooks(),
                        streaming=streaming,
                        tool_schema_mode="full",
                    )

                    async def responses():
                        if streaming:
                            yield LLMResponse(
                                role="assistant", completion_text="测试", is_chunk=True
                            )
                        yield LLMResponse(role="assistant", completion_text="测试回复")

                    with patch.object(runner, "_iter_llm_responses_with_fallback", responses):
                        results = [response async for response in runner.step()]
                    self.assertTrue(runner.done())
                    self.assertEqual(runner.get_final_llm_resp().completion_text, "测试回复")
                    self.assertTrue(any(response.type == "agent_stats" for response in results))
                    self.assertEqual(context.messages[0].role, "user")
                    self.assertGreater(runner.stats.start_time, 0)
