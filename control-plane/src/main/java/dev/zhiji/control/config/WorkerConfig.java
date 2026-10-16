package dev.zhiji.control.config;

import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.client.RestClient;

/**
 * Spring configuration for the control plane.
 */
@Configuration
@EnableConfigurationProperties(WorkerProperties.class)
public class WorkerConfig {

    @Bean
    public RestClient workerRestClient(WorkerProperties props) {
        return RestClient.builder()
            .baseUrl(props.baseUrl())
            .build();
    }
}
