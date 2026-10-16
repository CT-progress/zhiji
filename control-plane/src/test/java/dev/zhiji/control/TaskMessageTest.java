package dev.zhiji.control;

import dev.zhiji.control.client.WorkerClient;
import dev.zhiji.control.config.RabbitMQConfig;
import dev.zhiji.control.model.TaskDispatch;
import dev.zhiji.control.model.TaskEvent;
import dev.zhiji.control.model.TaskMessage;
import dev.zhiji.control.service.TaskMessageConsumer;
import dev.zhiji.control.service.TaskMessageProducer;
import dev.zhiji.control.service.TaskService;
import org.junit.jupiter.api.*;
import org.mockito.ArgumentCaptor;
import org.springframework.amqp.core.MessagePostProcessor;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.mock.mockito.MockBean;

import java.time.Instant;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

/**
 * Phase 6.4 message orchestration tests: producer, consumer, idempotency, DLQ.
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.MOCK)
@AutoConfigureMockMvc
class TaskMessageTest {

    @Autowired
    private TaskMessageProducer producer;
    @Autowired
    private TaskMessageConsumer consumer;
    @Autowired
    private TaskService taskService;

    @MockBean
    private RabbitTemplate rabbitTemplate;
    @MockBean
    private WorkerClient workerClient;

    // -----------------------------------------------------------------------
    // Producer
    // -----------------------------------------------------------------------

    @Test
    @DisplayName("producer: publishes message with correct exchange, routing key, and content")
    void producer_publishesWithCorrectArguments() {
        TaskDispatch dispatch = new TaskDispatch(
            TaskDispatch.SCHEMA_VERSION, "task-prod-1", "user-1",
            "note.generate", "douyin", Map.of("url", "https://v.douyin.com/test"),
            "trace-prod-1", 1, 0
        );

        producer.publish(dispatch);

        ArgumentCaptor<Object> payloadCaptor = ArgumentCaptor.forClass(Object.class);
        verify(rabbitTemplate).convertAndSend(
            eq(RabbitMQConfig.TASK_EXCHANGE),
            eq(RabbitMQConfig.DISPATCH_ROUTING_KEY),
            payloadCaptor.capture(),
            any(MessagePostProcessor.class)
        );

        TaskMessage sent = (TaskMessage) payloadCaptor.getValue();
        assertEquals("task-prod-1", sent.taskId());
        assertEquals("douyin", sent.platform());
        assertEquals("trace-prod-1", sent.traceId());
    }

    // -----------------------------------------------------------------------
    // Consumer: normal dispatch
    // -----------------------------------------------------------------------

    @Test
    @DisplayName("consumer: dispatches to worker for pending task")
    void consumer_dispatchesForPendingTask() {
        String taskId = "task-consume-1";
        taskService.registerDispatch(taskId, "trace-1");

        TaskMessage message = new TaskMessage(
            taskId, "user-1", "note.generate", "douyin",
            Map.of(), "trace-1", 1, 0
        );

        consumer.consume(message);

        ArgumentCaptor<TaskDispatch> captor = ArgumentCaptor.forClass(TaskDispatch.class);
        verify(workerClient).dispatch(captor.capture());
        assertEquals(taskId, captor.getValue().taskId());
        assertEquals("trace-1", captor.getValue().traceId());
    }

    // -----------------------------------------------------------------------
    // Consumer: idempotency (skip running task)
    // -----------------------------------------------------------------------

    @Test
    @DisplayName("consumer: skips dispatch for already-running task")
    void consumer_skipsForRunningTask() {
        String taskId = "task-consume-2";
        taskService.registerDispatch(taskId, "trace-2");
        taskService.handleEvent(new TaskEvent(
            TaskEvent.SCHEMA_VERSION, taskId, TaskEvent.TASK_STARTED,
            "trace-2", Instant.now(), Map.of()
        ));

        TaskMessage message = new TaskMessage(
            taskId, "user-1", "note.generate", "douyin",
            Map.of(), "trace-2", 1, 0
        );

        consumer.consume(message);

        verify(workerClient, never()).dispatch(any());
    }

    @Test
    @DisplayName("consumer: skips dispatch for succeeded task")
    void consumer_skipsForSucceededTask() {
        String taskId = "task-consume-3";
        taskService.registerDispatch(taskId, "trace-3");
        taskService.handleEvent(new TaskEvent(
            TaskEvent.SCHEMA_VERSION, taskId, TaskEvent.TASK_STARTED,
            "trace-3", Instant.now(), Map.of()
        ));
        taskService.handleEvent(new TaskEvent(
            TaskEvent.SCHEMA_VERSION, taskId, TaskEvent.TASK_SUCCEEDED,
            "trace-3", Instant.now(), Map.of()
        ));

        TaskMessage message = new TaskMessage(
            taskId, "user-1", "note.generate", "douyin",
            Map.of(), "trace-3", 1, 0
        );

        consumer.consume(message);

        verify(workerClient, never()).dispatch(any());
    }

    // -----------------------------------------------------------------------
    // Consumer: failure triggers retry (exception propagates)
    // -----------------------------------------------------------------------

    @Test
    @DisplayName("consumer: rethrows on worker failure to trigger retry")
    void consumer_rethrowsOnFailure() {
        String taskId = "task-consume-4";
        taskService.registerDispatch(taskId, "trace-4");

        TaskMessage message = new TaskMessage(
            taskId, "user-1", "note.generate", "douyin",
            Map.of(), "trace-4", 1, 0
        );

        doThrow(new RuntimeException("worker down"))
            .when(workerClient).dispatch(any());

        assertThrows(RuntimeException.class, () -> consumer.consume(message));
    }

    // -----------------------------------------------------------------------
    // DLQ handler
    // -----------------------------------------------------------------------

    @Test
    @DisplayName("DLQ: handler clears task state")
    void dlq_clearsTaskState() {
        String taskId = "task-dlq-1";
        taskService.registerDispatch(taskId, "trace-dlq");

        TaskMessage message = new TaskMessage(
            taskId, "user-1", "note.generate", "douyin",
            Map.of(), "trace-dlq", 1, 0
        );

        consumer.handleDlq(message);

        assertNull(taskService.get(taskId));
    }

    @Test
    @DisplayName("DLQ: handler handles unknown task without error")
    void dlq_handlesUnknownTask() {
        TaskMessage message = new TaskMessage(
            "unknown-task", "user-1", "note.generate", "douyin",
            Map.of(), "trace-unknown", 1, 0
        );

        assertDoesNotThrow(() -> consumer.handleDlq(message));
    }
}
