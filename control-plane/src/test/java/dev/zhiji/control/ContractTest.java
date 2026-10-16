package dev.zhiji.control;

import dev.zhiji.control.client.WorkerClient;
import dev.zhiji.control.config.WorkerProperties;
import dev.zhiji.control.model.*;
import dev.zhiji.control.service.TaskService;
import org.junit.jupiter.api.*;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.*;
import org.springframework.test.web.client.MockRestServiceServer;
import org.springframework.web.client.RestClient;

import java.time.Instant;
import java.util.Map;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.*;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.*;
import static org.springframework.test.web.client.response.MockRestResponseCreators.*;

/**
 * Phase 6.1 contract tests for the Java control plane.
 *
 * <p>Verifies the control plane side of {@code docs/contracts/worker-api.yaml}:
 * <ul>
 *   <li>Callback controller processes TaskEvent correctly</li>
 *   <li>WorkerClient sends dispatch/cancel/health to worker with trace id</li>
 *   <li>Task state transitions: pending -> running -> succeeded/failed</li>
 *   <li>Schema version and event-type constants match the OpenAPI contract</li>
 * </ul>
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.NONE)
class ContractTest {

    @Autowired
    private TaskService taskService;

    // -----------------------------------------------------------------------
    // Callback API: worker -> control plane
    // -----------------------------------------------------------------------

    @Test
    @DisplayName("callback: task.started sets status to running")
    void callback_taskStarted_setsRunning() {
        String taskId = registerTask();
        TaskEvent event = makeEvent(taskId, TaskEvent.TASK_STARTED, Map.of("type", "note.generate"));
        TaskService.TaskState state = taskService.handleEvent(event);
        assertNotNull(state);
        assertEquals("running", state.status());
        assertEquals("task.started", state.lastEvent());
    }

    @Test
    @DisplayName("callback: step.progress updates progress")
    void callback_stepProgress_updatesProgress() {
        String taskId = registerTask();
        taskService.handleEvent(makeEvent(taskId, TaskEvent.TASK_STARTED, Map.of()));
        TaskEvent progressEvent = makeEvent(taskId, TaskEvent.STEP_PROGRESS,
            Map.of("step", "fetch", "progress", 30, "message", "fetching content"));
        TaskService.TaskState state = taskService.handleEvent(progressEvent);
        assertEquals(30, state.progress());
        assertEquals("step.progress", state.lastEvent());
    }

    @Test
    @DisplayName("callback: task.succeeded sets terminal status succeeded")
    void callback_taskSucceeded_setsSucceeded() {
        String taskId = registerTask();
        taskService.handleEvent(makeEvent(taskId, TaskEvent.TASK_STARTED, Map.of()));
        TaskEvent doneEvent = makeEvent(taskId, TaskEvent.TASK_SUCCEEDED,
            Map.of("result", Map.of("title", "test", "path", "/tmp/test.md")));
        TaskService.TaskState state = taskService.handleEvent(doneEvent);
        assertEquals("succeeded", state.status());
    }

    @Test
    @DisplayName("callback: task.failed sets terminal status failed with error")
    void callback_taskFailed_setsFailed() {
        String taskId = registerTask();
        taskService.handleEvent(makeEvent(taskId, TaskEvent.TASK_STARTED, Map.of()));
        TaskEvent failEvent = makeEvent(taskId, TaskEvent.TASK_FAILED,
            Map.of("error", "pipeline crashed", "step", "generate"));
        TaskService.TaskState state = taskService.handleEvent(failEvent);
        assertEquals("failed", state.status());
        assertEquals("pipeline crashed", state.error());
    }

    @Test
    @DisplayName("callback: unknown task returns null")
    void callback_unknownTask_returnsNull() {
        TaskEvent event = makeEvent(UUID.randomUUID().toString(), TaskEvent.TASK_STARTED, Map.of());
        TaskService.TaskState state = taskService.handleEvent(event);
        assertNull(state);
    }

    // -----------------------------------------------------------------------
    // Worker API: control plane -> worker (via WorkerClient)
    // -----------------------------------------------------------------------

    @Test
    @DisplayName("dispatch: WorkerClient sends POST with trace id to worker")
    void workerClient_dispatch_sendsPostWithTraceId() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer mockServer = MockRestServiceServer.bindTo(builder).build();
        WorkerClient client = new WorkerClient(
            builder.baseUrl("http://localhost").build(),
            new WorkerProperties("http://localhost")
        );

        String taskId = UUID.randomUUID().toString();
        String traceId = UUID.randomUUID().toString();
        TaskDispatch dispatch = new TaskDispatch(
            TaskDispatch.SCHEMA_VERSION, taskId, "user-1", "note.generate",
            "douyin", Map.of("url", "https://v.douyin.com/test"),
            traceId, 1, 0
        );

        mockServer.expect(requestTo(org.hamcrest.Matchers.containsString("/internal/v1/tasks:dispatch")))
            .andExpect(method(HttpMethod.POST))
            .andExpect(header("X-Trace-Id", traceId))
            .andRespond(withSuccess(
                "{\"accepted\":true,\"taskId\":\"" + taskId + "\"}",
                MediaType.APPLICATION_JSON));

        DispatchResponse resp = client.dispatch(dispatch);
        assertNotNull(resp);
        assertTrue(resp.accepted());
        assertEquals(taskId, resp.taskId());
        mockServer.verify();
    }

    @Test
    @DisplayName("cancel: WorkerClient sends POST to worker cancel endpoint")
    void workerClient_cancel_sendsPost() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer mockServer = MockRestServiceServer.bindTo(builder).build();
        WorkerClient client = new WorkerClient(
            builder.baseUrl("http://localhost").build(),
            new WorkerProperties("http://localhost")
        );

        String taskId = UUID.randomUUID().toString();
        String traceId = UUID.randomUUID().toString();

        mockServer.expect(requestTo(org.hamcrest.Matchers.containsString(":cancel")))
            .andExpect(method(HttpMethod.POST))
            .andExpect(header("X-Trace-Id", traceId))
            .andRespond(withSuccess(
                "{\"cancelled\":true,\"taskId\":\"" + taskId + "\"}",
                MediaType.APPLICATION_JSON));

        CancelResponse resp = client.cancel(taskId, traceId);
        assertNotNull(resp);
        assertTrue(resp.cancelled());
        mockServer.verify();
    }

    @Test
    @DisplayName("health: WorkerClient GET returns worker health status")
    void workerClient_health_returnsStatus() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer mockServer = MockRestServiceServer.bindTo(builder).build();
        WorkerClient client = new WorkerClient(
            builder.baseUrl("http://localhost").build(),
            new WorkerProperties("http://localhost")
        );

        mockServer.expect(requestTo(org.hamcrest.Matchers.containsString("/worker/health")))
            .andExpect(method(HttpMethod.GET))
            .andRespond(withSuccess(
                "{\"status\":\"ok\",\"version\":\"0.1.0\",\"capabilities\":[\"note.generate\"]}",
                MediaType.APPLICATION_JSON));

        WorkerHealth health = client.health();
        assertNotNull(health);
        assertEquals("ok", health.status());
        assertTrue(health.capabilities().contains("note.generate"));
        mockServer.verify();
    }

    @Test
    @DisplayName("dispatch: worker failure throws exception")
    void workerClient_dispatchFailure_throwsException() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer mockServer = MockRestServiceServer.bindTo(builder).build();
        WorkerClient client = new WorkerClient(
            builder.baseUrl("http://localhost").build(),
            new WorkerProperties("http://localhost")
        );

        mockServer.expect(requestTo(org.hamcrest.Matchers.containsString("/internal/v1/tasks:dispatch")))
            .andExpect(method(HttpMethod.POST))
            .andRespond(withStatus(HttpStatus.INTERNAL_SERVER_ERROR));

        TaskDispatch dispatch = new TaskDispatch(
            TaskDispatch.SCHEMA_VERSION, "task-x", null, "note.generate",
            null, Map.of(), null, 1, 0
        );
        assertThrows(Exception.class, () -> client.dispatch(dispatch));
        mockServer.verify();
    }

    // -----------------------------------------------------------------------
    // TaskState transitions
    // -----------------------------------------------------------------------

    @Test
    @DisplayName("transition: pending -> running -> succeeded")
    void stateTransition_pendingToRunningToSucceeded() {
        String taskId = "task-" + UUID.randomUUID();
        taskService.registerDispatch(taskId, "trace-" + UUID.randomUUID());

        assertEquals("pending", taskService.get(taskId).status());

        taskService.handleEvent(makeEvent(taskId, TaskEvent.TASK_STARTED, Map.of()));
        assertEquals("running", taskService.get(taskId).status());

        taskService.handleEvent(makeEvent(taskId, TaskEvent.TASK_SUCCEEDED, Map.of()));
        assertEquals("succeeded", taskService.get(taskId).status());
    }

    @Test
    @DisplayName("transition: pending -> running -> failed")
    void stateTransition_pendingToRunningToFailed() {
        String taskId = "task-" + UUID.randomUUID();
        taskService.registerDispatch(taskId, "trace-" + UUID.randomUUID());

        taskService.handleEvent(makeEvent(taskId, TaskEvent.TASK_STARTED, Map.of()));
        assertEquals("running", taskService.get(taskId).status());

        taskService.handleEvent(makeEvent(taskId, TaskEvent.TASK_FAILED, Map.of("error", "OOM")));
        assertEquals("failed", taskService.get(taskId).status());
        assertEquals("OOM", taskService.get(taskId).error());
    }

    @Test
    @DisplayName("transition: step events do not override terminal status")
    void stateTransition_stepEventsDoNotChangeTerminalState() {
        String taskId = "task-" + UUID.randomUUID();
        taskService.registerDispatch(taskId, "trace-1");

        taskService.handleEvent(makeEvent(taskId, TaskEvent.TASK_SUCCEEDED, Map.of()));
        assertEquals("succeeded", taskService.get(taskId).status());

        taskService.handleEvent(makeEvent(taskId, TaskEvent.STEP_PROGRESS,
            Map.of("step", "late", "progress", 99)));
        assertEquals("succeeded", taskService.get(taskId).status());
    }

    @Test
    @DisplayName("transition: registerDispatch sets initial pending state with zero progress")
    void stateTransition_initialStateIsPending() {
        String taskId = "task-" + UUID.randomUUID();
        String traceId = "trace-" + UUID.randomUUID();
        TaskService.TaskState state = taskService.registerDispatch(taskId, traceId);

        assertEquals("pending", state.status());
        assertEquals(0, state.progress());
        assertNull(state.lastEvent());
        assertNull(state.error());
        assertEquals(traceId, state.traceId());
    }

    // -----------------------------------------------------------------------
    // Contract schema validation
    // -----------------------------------------------------------------------

    @Test
    @DisplayName("contract: TaskDispatch defaults schemaVersion to 1.0")
    void contract_dispatchSchemaVersionDefaultsTo10() {
        TaskDispatch d = new TaskDispatch(null, null, "user-1", "note.generate",
            "douyin", Map.of(), null, 0, 0);
        assertEquals("1.0", d.schemaVersion());
        assertNotNull(d.taskId());
        assertNotNull(d.traceId());
        assertEquals(1, d.attempt());
    }

    @Test
    @DisplayName("contract: TaskEvent type constants match OpenAPI enum")
    void contract_eventTypeConstantsMatchOpenApi() {
        assertEquals("task.started", TaskEvent.TASK_STARTED);
        assertEquals("step.progress", TaskEvent.STEP_PROGRESS);
        assertEquals("step.succeeded", TaskEvent.STEP_SUCCEEDED);
        assertEquals("step.failed", TaskEvent.STEP_FAILED);
        assertEquals("task.succeeded", TaskEvent.TASK_SUCCEEDED);
        assertEquals("task.failed", TaskEvent.TASK_FAILED);
    }

    @Test
    @DisplayName("contract: schema version is 1.0")
    void contract_schemaVersionIs10() {
        assertEquals("1.0", TaskDispatch.SCHEMA_VERSION);
        assertEquals("1.0", TaskEvent.SCHEMA_VERSION);
    }

    // -----------------------------------------------------------------------
    // Helpers
    // -----------------------------------------------------------------------

    private String registerTask() {
        String taskId = "task-" + UUID.randomUUID();
        taskService.registerDispatch(taskId, "trace-" + UUID.randomUUID());
        return taskId;
    }

    private TaskEvent makeEvent(String taskId, String eventType, Map<String, Object> data) {
        return new TaskEvent(
            TaskEvent.SCHEMA_VERSION, taskId, eventType,
            "trace-" + taskId, Instant.now(), data
        );
    }
}
