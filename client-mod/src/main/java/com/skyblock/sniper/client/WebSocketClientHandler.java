package com.skyblock.sniper.client;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

public class WebSocketClientHandler {
    public static void onMessage(String message) {
        if (message == null || message.trim().isEmpty()) {
            return;
        }

        try {
            JsonObject json = JsonParser.parseString(message).getAsJsonObject();
            if (json.has("uuid")) {
                String auctionUuid = json.get("uuid").getAsString();
                double profit = json.has("profit") ? json.get("profit").getAsDouble() : 0.0;
                String confidence = json.has("confidence_label") ? json.get("confidence_label").getAsString() : "MEDIUM";
                AutoInspectManager.processFlip(auctionUuid, profit, confidence);
            }
        } catch (Exception e) {
            AhSniperClient.LOGGER.error("[AH Sniper] Failed to parse WebSocket payload in handler: {}", e.getMessage());
        }
    }
}
