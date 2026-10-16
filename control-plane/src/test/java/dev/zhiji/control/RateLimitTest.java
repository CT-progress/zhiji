package dev.zhiji.control;

import com.fasterxml.jackson.databind.ObjectMapper;
import dev.zhiji.control.dto.*;
import dev.zhiji.control.service.InMemoryRateLimiter;
import org.junit.jupiter.api.*;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;

import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

/**
 * Phase 6.3 rate limiting tests: per-platform QPS limits, 429 responses,
 * cross-platform independence.
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.MOCK)
@AutoConfigureMockMvc
class RateLimitTest {

    @Autowired
    private MockMvc mockMvc;
    @Autowired
    private ObjectMapper objectMapper;
    @Autowired
    private InMemoryRateLimiter rateLimiter;

    @AfterEach
    void cleanup() {
        rateLimiter.clear();
    }

    @Test
    @DisplayName("rate limit: requests within QPS pass the limiter (return 502 from no worker)")
    void rateLimit_withinQps_passesLimiter() throws Exception {
        String token = registerAndLogin("rluser1", "password123");
        String body = taskBody("douyin");

        // douyin QPS = 3; first 3 requests pass rate limiter, then hit no worker (502)
        for (int i = 0; i < 3; i++) {
            mockMvc.perform(post("/api/v1/tasks")
                    .header("Authorization", "Bearer " + token)
                    .contentType(MediaType.APPLICATION_JSON)
                    .content(body))
                .andExpect(status().isBadGateway());
        }
    }

    @Test
    @DisplayName("rate limit: exceeding QPS returns 429")
    void rateLimit_exceedingQps_returns429() throws Exception {
        String token = registerAndLogin("rluser2", "password123");
        String body = taskBody("douyin");

        // Exhaust douyin bucket (QPS=3)
        for (int i = 0; i < 3; i++) {
            mockMvc.perform(post("/api/v1/tasks")
                    .header("Authorization", "Bearer " + token)
                    .contentType(MediaType.APPLICATION_JSON)
                    .content(body))
                .andReturn();
        }

        // 4th request should be rate limited
        mockMvc.perform(post("/api/v1/tasks")
                .header("Authorization", "Bearer " + token)
                .contentType(MediaType.APPLICATION_JSON)
                .content(body))
            .andExpect(status().isTooManyRequests())
            .andExpect(jsonPath("$.error").value("rate_limited"));
    }

    @Test
    @DisplayName("rate limit: different platforms are independent")
    void rateLimit_differentPlatforms_independent() throws Exception {
        String token = registerAndLogin("rluser3", "password123");

        // Exhaust douyin bucket (QPS=3)
        String douyinBody = taskBody("douyin");
        for (int i = 0; i < 3; i++) {
            mockMvc.perform(post("/api/v1/tasks")
                    .header("Authorization", "Bearer " + token)
                    .contentType(MediaType.APPLICATION_JSON)
                    .content(douyinBody))
                .andReturn();
        }

        // Douyin is now rate limited
        mockMvc.perform(post("/api/v1/tasks")
                .header("Authorization", "Bearer " + token)
                .contentType(MediaType.APPLICATION_JSON)
                .content(douyinBody))
            .andExpect(status().isTooManyRequests());

        // Bilibili (QPS=5) is still available -- should pass limiter (502 from no worker)
        String bilibiliBody = taskBody("bilibili");
        mockMvc.perform(post("/api/v1/tasks")
                .header("Authorization", "Bearer " + token)
                .contentType(MediaType.APPLICATION_JSON)
                .content(bilibiliBody))
            .andExpect(status().isBadGateway());
    }

    @Test
    @DisplayName("rate limit: 429 returned without contacting worker")
    void rateLimit_429WithoutWorker() throws Exception {
        String token = registerAndLogin("rluser4", "password123");
        String body = taskBody("douyin");

        // Exhaust douyin bucket
        for (int i = 0; i < 3; i++) {
            mockMvc.perform(post("/api/v1/tasks")
                    .header("Authorization", "Bearer " + token)
                    .contentType(MediaType.APPLICATION_JSON)
                    .content(body))
                .andReturn();
        }

        // 4th request gets 429 -- response should be fast and not 502
        long start = System.nanoTime();
        mockMvc.perform(post("/api/v1/tasks")
                .header("Authorization", "Bearer " + token)
                .contentType(MediaType.APPLICATION_JSON)
                .content(body))
            .andExpect(status().isTooManyRequests());
        long elapsed = (System.nanoTime() - start) / 1_000_000;
        // Should be well under the typical 502 timeout
        assertTrue(elapsed < 5000, "429 response should be fast, took " + elapsed + "ms");
    }

    @Test
    @DisplayName("rate limit: unauthenticated request returns 401 before rate limiting")
    void rateLimit_unauthenticated_returns401() throws Exception {
        mockMvc.perform(post("/api/v1/tasks")
                .contentType(MediaType.APPLICATION_JSON)
                .content(taskBody("douyin")))
            .andExpect(status().isUnauthorized());
    }

    // -----------------------------------------------------------------------
    // Helpers
    // -----------------------------------------------------------------------

    private String registerAndLogin(String username, String password) throws Exception {
        MvcResult result = mockMvc.perform(post("/api/v1/auth/register")
                .contentType(MediaType.APPLICATION_JSON)
                .content(objectMapper.writeValueAsString(new RegisterRequest(username, password, null))))
            .andReturn();
        TokenResponse tokens = objectMapper.readValue(
            result.getResponse().getContentAsString(), TokenResponse.class);
        return tokens.accessToken();
    }

    private String taskBody(String platform) {
        return "{\"type\":\"note.generate\",\"platform\":\"" + platform + "\","
            + "\"input\":{\"url\":\"https://v.douyin.com/test\"},"
            + "\"attempt\":1}";
    }
}
