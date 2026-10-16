package dev.zhiji.control.service;

import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import org.springframework.stereotype.Component;

/**
 * Micrometer metrics for task lifecycle.
 *
 * <p>Exposed via the Prometheus actuator endpoint for monitoring.
 */
@Component
public class TaskMetrics {

    private final Counter dispatched;
    private final Counter rateLimited;
    private final Counter workerFailure;
    private final Counter dlq;

    public TaskMetrics(MeterRegistry registry) {
        this.dispatched = Counter.builder("zhiji_tasks_dispatched_total")
            .description("Total tasks published to RabbitMQ")
            .register(registry);
        this.rateLimited = Counter.builder("zhiji_tasks_rate_limited_total")
            .description("Total requests rejected by rate limiter")
            .register(registry);
        this.workerFailure = Counter.builder("zhiji_tasks_worker_failures_total")
            .description("Total worker dispatch failures")
            .register(registry);
        this.dlq = Counter.builder("zhiji_tasks_dlq_total")
            .description("Total messages sent to dead-letter queue")
            .register(registry);
    }

    public void incDispatched() { dispatched.increment(); }
    public void incRateLimited() { rateLimited.increment(); }
    public void incWorkerFailure() { workerFailure.increment(); }
    public void incDlq() { dlq.increment(); }
}
