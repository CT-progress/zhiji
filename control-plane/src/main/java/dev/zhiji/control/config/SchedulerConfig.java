package dev.zhiji.control.config;

import org.springframework.context.annotation.Configuration;
import org.springframework.scheduling.annotation.EnableScheduling;

/**
 * Enables Spring scheduling for the outbox publisher.
 */
@Configuration
@EnableScheduling
public class SchedulerConfig {
}
