(() => {
    // 1. Thread & Session Setup
    const threadId = localStorage.getItem("thread_id") || crypto.randomUUID();
    localStorage.setItem("thread_id", threadId);

    // 2. Fallback Markdown Parser
    function fallbackMarkdown(md) {
        if (!md) return "";
        return md
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/^### (.*$)/gim, '<h3>$1</h3>')
            .replace(/^## (.*$)/gim, '<h2>$1</h2>')
            .replace(/^# (.*$)/gim, '<h1>$1</h1>')
            .replace(/\*\*(.*?)\*\*/gim, '<strong>$1</strong>')
            .replace(/\*(.*?)\*/gim, '<em>$1</em>')
            .replace(/^\s*[-*]\s+(.*$)/gim, '<li>$1</li>')
            .replace(/\n/gim, '<br>');
    }

    // 3. Robust Markdown Resolver using marked + DOMPurify
    function renderMarkdownToHtml(markdownString) {
        if (!markdownString) return "";

        if (window.marked) {
            try {
                const parsed = typeof window.marked.parse === "function"
                    ? window.marked.parse(markdownString, { gfm: true, breaks: true })
                    : window.marked(markdownString);

                if (typeof parsed === "string") {
                    return window.DOMPurify ? window.DOMPurify.sanitize(parsed) : parsed;
                }
            } catch (err) {
                console.error("Marked parsing error:", err);
            }
        }
        return fallbackMarkdown(markdownString);
    }

    // 4. Main DOM Logic
    document.addEventListener("DOMContentLoaded", () => {
        const sessionIdDisplay = document.getElementById("session-id-display");
        if (sessionIdDisplay) {
            sessionIdDisplay.innerText = threadId.slice(0, 8) + "...";
        }

        // Navigation Tabs
        document.querySelectorAll(".nav-btn").forEach(btn => {
            btn.addEventListener("click", () => {
                document.querySelectorAll(".nav-btn").forEach(b => b.classList.remove("active"));
                document.querySelectorAll(".view-panel").forEach(p => p.classList.remove("active"));
                btn.classList.add("active");
                const panel = document.getElementById(`${btn.dataset.tab}-view`);
                if (panel) panel.classList.add("active");
            });
        });

        // Purge Session
        const purgeBtn = document.getElementById("purge-btn");
        if (purgeBtn) {
            purgeBtn.addEventListener("click", () => {
                localStorage.removeItem("thread_id");
                location.reload();
            });
        }

        // Chat Components
        const chatContainer = document.getElementById("chat-stream");
        const inputField = document.getElementById("user-input");
        const sendBtn = document.getElementById("send-btn");

        async function sendMessage() {
            if (!inputField || !chatContainer) return;
            const text = inputField.value.trim();
            if (!text) return;
            inputField.value = "";

            if (sendBtn) sendBtn.disabled = true;
            inputField.disabled = true;

            // Render User Bubble
            const userMsg = document.createElement("div");
            userMsg.className = "chat-bubble user";
            userMsg.innerText = text;
            chatContainer.appendChild(userMsg);

            // Prepare Assistant Bubble Shell
            const assistantMsg = document.createElement("div");
            assistantMsg.className = "chat-bubble assistant";
            chatContainer.appendChild(assistantMsg);

            chatContainer.scrollTop = chatContainer.scrollHeight;

            try {
                const response = await fetch("/api/chat/stream", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ message: text, thread_id: threadId })
                });

                if (!response.ok) {
                    throw new Error(`Server returned HTTP ${response.status}`);
                }

                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                let streamBuffer = "";
                let assistantText = "";

                while (true) {
                    const { value, done } = await reader.read();
                    if (done) break;

                    streamBuffer += decoder.decode(value, { stream: true });
                    const parts = streamBuffer.split("\n\n");
                    streamBuffer = parts.pop() || "";

                    for (const part of parts) {
                        const line = part.trim();
                        if (!line.startsWith("data: ")) continue;

                        const rawData = line.slice(6).trim();
                        if (!rawData) continue;

                        let data;
                        try {
                            data = JSON.parse(rawData);
                        } catch (parseErr) {
                            continue;
                        }

                        // Collapsible Tool Invocation (Intent)
                        if (data.type === "tool_input") {
                            const trace = document.createElement("details");
                            trace.className = "trace-accordion intent";
                            trace.innerHTML = `
                                <summary>
                                    <span class="badge intent-badge">INTENT</span>
                                    <span class="tool-name">${data.name}</span>
                                </summary>
                                <div class="trace-content">
                                    <pre><code>${JSON.stringify(data.args, null, 2)}</code></pre>
                                </div>
                            `;
                            assistantMsg.appendChild(trace);

                        // Collapsible Tool Output (Data)
                        } else if (data.type === "tool_output") {
                            const trace = document.createElement("details");
                            trace.className = "trace-accordion output";
                            trace.innerHTML = `
                                <summary>
                                    <span class="badge output-badge">OUTPUT</span>
                                    <span class="tool-name">${data.name}</span>
                                </summary>
                                <div class="trace-content">
                                    <pre><code>${data.content}</code></pre>
                                </div>
                            `;
                            assistantMsg.appendChild(trace);

                        // Synthesized Markdown Content
                        } else if (data.type === "content") {
                            // Accumulate streaming text
                            assistantText += data.text;

                            let textNode = assistantMsg.querySelector(".agent-reply");
                            if (!textNode) {
                                textNode = document.createElement("div");
                                textNode.className = "agent-reply markdown-body";
                                assistantMsg.appendChild(textNode);
                            }

                            textNode.innerHTML = renderMarkdownToHtml(assistantText);

                        // Backend Stream Error
                        } else if (data.type === "error") {
                            const errBlock = document.createElement("div");
                            errBlock.className = "trace-block error";
                            errBlock.innerHTML = `<div class="trace-title">⚠️ Error</div><pre>${data.detail}</pre>`;
                            assistantMsg.appendChild(errBlock);
                        }

                        chatContainer.scrollTop = chatContainer.scrollHeight;
                    }
                }
            } catch (err) {
                const errorMsg = document.createElement("div");
                errorMsg.className = "trace-block error";
                errorMsg.innerText = `Stream connection error: ${err.message}`;
                assistantMsg.appendChild(errorMsg);
            } finally {
                if (inputField) {
                    inputField.disabled = false;
                    inputField.focus();
                }
                if (sendBtn) sendBtn.disabled = false;
            }
        }

        if (sendBtn) sendBtn.addEventListener("click", sendMessage);
        if (inputField) {
            inputField.addEventListener("keydown", (e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    sendMessage();
                }
            });
        }

        // Admin Audit Form Query
        const auditForm = document.getElementById("admin-auth-form");
        if (auditForm) {
            auditForm.addEventListener("submit", async (e) => {
                e.preventDefault();
                const usernameInput = document.getElementById("admin-user");
                const passwordInput = document.getElementById("admin-pass");
                const wrapper = document.getElementById("audit-table-wrapper");
                if (!wrapper) return;

                wrapper.innerHTML = "<p>Loading logs...</p>";
                try {
                    const res = await fetch("/api/audit/logs", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({
                            username: usernameInput ? usernameInput.value : "",
                            password: passwordInput ? passwordInput.value : ""
                        })
                    });
                    const resData = await res.json();
                    if (!res.ok) {
                        wrapper.innerHTML = `<p class="error-text" style="color: #f87171; padding: 10px;">${resData.detail || "Authentication Failed"}</p>`;
                        return;
                    }

                    const logs = resData.logs || [];
                    if (logs.length === 0) {
                        wrapper.innerHTML = "<p style='padding: 10px;'>No logs found.</p>";
                        return;
                    }

                    let tableHtml = `
                        <table class="audit-table">
                            <thead>
                                <tr>
                                    <th>Timestamp</th>
                                    <th>Session ID</th>
                                    <th>Node</th>
                                    <th>Tool</th>
                                    <th>Content</th>
                                </tr>
                            </thead>
                            <tbody>
                    `;
                    for (const row of logs) {
                        tableHtml += `
                            <tr>
                                <td>${row.Timestamp || ""}</td>
                                <td>${row.SessionID || ""}</td>
                                <td>${row.NodeExecuted || ""}</td>
                                <td>${row.ToolName || ""}</td>
                                <td><pre>${row.Content || ""}</pre></td>
                            </tr>
                        `;
                    }
                    tableHtml += `</tbody></table>`;
                    wrapper.innerHTML = tableHtml;
                } catch (auditErr) {
                    wrapper.innerHTML = `<p class="error-text" style="color: #f87171; padding: 10px;">${auditErr.message}</p>`;
                }
            });
        }
    });
})();