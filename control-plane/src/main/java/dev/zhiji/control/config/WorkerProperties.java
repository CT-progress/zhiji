package dev.zhiji.control.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * Configuration for the Python worker connection.
 */
@ConfigurationProperties(prefix = "zhiji.worker")
public record WorkerProperties(String baseUrl) {}
