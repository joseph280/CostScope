defmodule CostscopeWebWeb.ChatLive do
  @moduledoc """
  Chat UI for CostScope.

  State:
    :messages — list of message maps (role + text + optional sql metadata)
    :input    — current text in the question box
    :pending  — true while a question is in flight

  Flow:
    1. User submits a question
    2. We append it to messages, kick off a Task that POSTs to the
       Python backend, and set pending=true
    3. Task sends {:assistant_response, result} back to self()
    4. handle_info/2 appends the assistant message, clears pending
  """

  use CostscopeWebWeb, :live_view

  alias CostscopeWeb.Backend

  @impl true
  def mount(_params, _session, socket) do
    {:ok,
      socket
      |> assign(:messages, [])
      |> assign(:input, "")
      |> assign(:pending, false)
    }
  end

  @impl true
  def handle_event("send", %{"question" => ""}, socket) do
    # Ignore empty submissions
    {:noreply, socket}
  end

  @impl true
  def handle_event("send", %{"question" => question}, socket) when is_binary(question) do
    parent = self()

    # Fire off the backend call in a separate process so we don't
    # block the LiveView's message loop. The result comes back via
    # handle_info/2.

    Task.start(fn ->
      result = Backend.chat(question)
      send(parent, {:assistant_response, result})
    end)

    user_msg = %{role: :user, text: question}

    socket =
      socket
      |> update(:messages, &(&1 ++ [user_msg]))
      |> assign(:input, "")
      |> assign(:pending, true)

    {:noreply, socket}
  end

  @impl true
  def handle_event("input_change", %{"question" => q}, socket) do
    {:noreply, assign(socket, :input, q)}
  end

  @impl true
  def handle_info({:assistant_response, {:ok, body}}, socket) do
    msg = %{
      role: :assistant,
      text: body["answer"] || "(no answer)",
      used_data: body["used_data"],
      sql: body["sql"],
      row_count: body["row_count"],
      cost_usd: body["cost_usd"],
      latency_ms: body["latency_ms"],
      claude_calls: body["claude_calls"]
    }

    socket =
      socket
      |> update(:messages, &(&1 ++ [msg]))
      |> assign(:pending, false)

    {:noreply, socket}
  end

  @impl true
  def handle_info({:assistant_response, {:error, reason}}, socket) do
    msg = %{
      role: :assistant,
      text: "Sorry — something went wrong. #{inspect(reason)}",
      error: true
    }
    socket =
      socket
      |> update(:messages, &(&1 ++ [msg]))
      |> assign(:pending, false)

    {:noreply, socket}
  end


  # Render

  @impl true
  def render(assigns) do
    ~H"""
    <div style="min-height: 100vh; background: #f7f8fa; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;">
      <div style="max-width: 760px; margin: 0 auto; padding: 32px 20px 120px;">

        <header style="margin-bottom: 32px; padding-bottom: 16px; border-bottom: 1px solid #e5e7eb;">
          <h1 style="margin: 0; font-size: 28px; font-weight: 700; color: #0f172a; letter-spacing: -0.02em;">
            CostScope
          </h1>
          <p style="margin: 6px 0 0; color: #64748b; font-size: 14px;">
            Natural-language questions about your enterprise cost data.
          </p>
        </header>

        <div id="messages" style="display: flex; flex-direction: column; gap: 16px; min-height: 200px; margin-bottom: 24px;">
          <%= for msg <- @messages do %>
            <%= if msg.role == :user do %>
              <div style="align-self: flex-end; max-width: 80%; background: #1e40af; color: white; padding: 12px 16px; border-radius: 16px 16px 4px 16px; font-size: 15px; line-height: 1.5; box-shadow: 0 1px 2px rgba(0,0,0,0.05);">
                <%= msg.text %>
              </div>
            <% else %>
              <div style="align-self: flex-start; max-width: 88%; background: white; border: 1px solid #e5e7eb; padding: 16px 18px; border-radius: 16px 16px 16px 4px; box-shadow: 0 1px 2px rgba(0,0,0,0.04);">
                <div style={
                  if msg[:error],
                    do: "color: #b91c1c; font-size: 15px; line-height: 1.6;",
                    else: "color: #0f172a; font-size: 15px; line-height: 1.6;"
                }>
                  <%= msg.text %>
                </div>

                <%= if msg[:sql] do %>
                  <details style="margin-top: 14px; padding-top: 14px; border-top: 1px solid #f1f5f9;">
                    <summary style="cursor: pointer; color: #64748b; font-size: 12px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; user-select: none;">
                      <span style="color: #1e40af; font-weight: 600;">SQL</span>
                      <span style="margin: 0 8px; color: #cbd5e1;">·</span>
                      <%= msg.row_count %> rows
                      <span style="margin: 0 8px; color: #cbd5e1;">·</span>
                      <%= msg.latency_ms %> ms
                      <span style="margin: 0 8px; color: #cbd5e1;">·</span>
                      $<%= :erlang.float_to_binary(msg.cost_usd || 0.0, decimals: 4) %>
                      <span style="margin: 0 8px; color: #cbd5e1;">·</span>
                      <%= msg.claude_calls %> Claude calls
                    </summary>
                    <pre style="margin-top: 10px; padding: 12px 14px; background: #0f172a; color: #e2e8f0; border-radius: 8px; font-size: 12px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; line-height: 1.5; overflow-x: auto; white-space: pre-wrap;"><%= msg.sql %></pre>
                  </details>
                <% end %>
              </div>
            <% end %>
          <% end %>

          <%= if @pending do %>
            <div style="align-self: flex-start; background: white; border: 1px solid #e5e7eb; padding: 12px 16px; border-radius: 16px 16px 16px 4px;">
              <span style="display: inline-block; color: #94a3b8; font-style: italic; font-size: 14px;">
                thinking
                <span style="animation: pulse 1.4s ease-in-out infinite;">…</span>
              </span>
            </div>
          <% end %>
        </div>

        <form phx-submit="send" phx-change="input_change" style="position: fixed; bottom: 0; left: 0; right: 0; background: white; border-top: 1px solid #e5e7eb; padding: 16px 20px;">
          <div style="max-width: 760px; margin: 0 auto; display: flex; gap: 10px;">
            <input
              type="text"
              name="question"
              value={@input}
              placeholder="What was total marketing spend in Q4 2025?"
              autocomplete="off"
              autofocus
              disabled={@pending}
              style="flex: 1; padding: 12px 16px; border: 1px solid #cbd5e1; border-radius: 10px; font-size: 15px; outline: none; transition: border-color 0.15s; background: white; color: #0f172a;"
            />
            <button
              type="submit"
              disabled={@pending}
              style={
                if @pending,
                  do: "padding: 12px 24px; background: #94a3b8; color: white; border: 0; border-radius: 10px; font-size: 15px; font-weight: 600; cursor: not-allowed;",
                  else: "padding: 12px 24px; background: #1e40af; color: white; border: 0; border-radius: 10px; font-size: 15px; font-weight: 600; cursor: pointer;"
              }
            >
              Send
            </button>
          </div>
        </form>

      </div>

      <style>
        @keyframes pulse {
          0%, 100% { opacity: 0.3; }
          50% { opacity: 1; }
        }
        input:focus {
          border-color: #1e40af !important;
        }
        button[type="submit"]:hover:not(:disabled) {
          background: #1e3a8a !important;
        }
        details[open] summary {
          margin-bottom: 6px;
        }
      </style>
    </div>
    """
  end
end
