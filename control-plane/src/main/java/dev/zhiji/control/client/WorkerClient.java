package dev.zhiji.control.client;

import dev.zhiji.control.config.WorkerProperties;
import dev.zhiji.control.model.*;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

import java.util.UUID;

/**
 * HTTP client for talking to the Python worker.
 *
 * <p>Implements the Worker API side of the contract:
 * dispatch tasks, cancel tasks, check health.
 */
@Component
public class WorkerClient {

    private static final Logger log = LoggerFactory.getLogger(WorkerClient.class);

    private final RestClient restClient;
    private final WorkerProperties props;

    public WorkerClient(RestClient restClient, WorkerProperties props) {
        this.restClient = restClient;
        this.props = props;
    }

    /**
     * Dispatch a task to the worker.
     *
     * @return the worker's dispatch response, or null on failure
     */
    public DispatchResponse dispatch(TaskDispatch dispatch) {
        try {
            return restClient.post()
                .uri("/internal/v1/tasks:dispatch")
                .header("X-Trace-Id", dispatch.traceId())
                .body(dispatch)
                .retrieve()
                .body(DispatchResponse.class);
        } catch (Exception e) {
            log.error("failed to dispatch task {} to worker at {}: {}",
                dispatch.taskId(), props.baseUrl(), e.getMessage());
            return null;
        }
    }

    /**
     * Cancel a running task on the worker.
     */
    public CancelResponse cancel(String taskId, String traceId) {
        try {
            return restClient.post()
                .uri("/internal/v1/tasks/{taskId}:cancel", taskId)
                .header("X-Trace-Id", traceId)
                .retrieve()
                .body(CancelResponse.class);
        } catch (Exception e) {
            log.error("failed to cancel task {} on worker: {}", taskId, e.getMessage());
            return null;
        }
    }

    /**
     * Check worker health.
     */
    public WorkerHealth health() {
        try {
            return restClient.get()
                .uri("/internal/v1/worker/health")
                .retrieve()
                .body(WorkerHealth.class);
        } catch (Exception e) {
            log.error("worker health check failed: {}", e.getMessage());
            return new WorkerHealth("degraded", "unknown", java.util.List.of());
        }
    }
}
