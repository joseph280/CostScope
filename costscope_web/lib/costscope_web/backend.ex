defmodule CostscopeWeb.Backend do
  @moduledoc """
  HTTP client for the CostScope Python FastAPI backend.

  Phoenix never talks to Anthropic or Snowflake directly — all AI
  and data work lives behind this boundary. The Elixir side knows
  one URL and one JSON contract.
  """

  require Logger

  @doc """
  POST /chat with the user's question.

  Returns:
  {:ok, %{"answer" => _, "sql" => _, "used_data" => _, ...}}
  {:error, reason}
  """
  @spec chat(String.t()) :: {:ok, map()} | {:error, term()}
  def chat(question) when is_binary(question) do
    url = Application.fetch_env!(:costscope_web, :backend_url) <> "/chat"

    case Req.post(url,
      json: %{question: question},
      # Three Claude calls + Snowflake round trip is slow, so we need a long timeout.
      receive_timeout: 30_000,
      retry: false
    ) do
      {:ok, %Req.Response{status: 200, body: body}} when is_map(body) ->
        {:ok, body}

        {:ok, %Req.Response{status: status, body: body}} ->
          Logger.warning("backend_non_2xx status=#{status} body=#{inspect(body)}")
          {:error, {:http, status}}

        {:error, exception} ->
          Logger.warning("backend_request_failed err=#{inspect(exception)}")
          {:error, exception}

    end
  end
end
