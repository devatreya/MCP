"""OpenAI text generation helpers that handle chat/responses differences."""


def _model_prefers_responses(model_name):
    name = (model_name or "").strip().lower()
    return ("gpt-5" in name) or ("codex" in name)


def _is_responses_only_error(exc):
    msg = str(exc).lower()
    return "only supported in v1/responses" in msg


def _is_temperature_unsupported_error(exc):
    msg = str(exc).lower()
    return "temperature" in msg and (
        "not supported" in msg or "unsupported" in msg or "invalid" in msg
    )


def _is_model_not_found_error(exc):
    msg = str(exc).lower()
    return (
        "model_not_found" in msg
        or "does not exist" in msg
        or "do not have access" in msg
    )


def _extract_responses_text(response):
    text = getattr(response, "output_text", None)
    if isinstance(text, str) and text.strip():
        return text.strip()

    output_items = getattr(response, "output", None)
    if isinstance(output_items, list):
        chunks = []
        for item in output_items:
            content_items = getattr(item, "content", None)
            if not isinstance(content_items, list):
                continue
            for content in content_items:
                ctype = getattr(content, "type", "")
                if ctype not in {"output_text", "text"}:
                    continue
                ctext = getattr(content, "text", None)
                if isinstance(ctext, str) and ctext:
                    chunks.append(ctext)
        if chunks:
            return "\n".join(chunks).strip()

    raise RuntimeError("Responses API returned no text output.")


def _messages_to_responses_input(messages):
    payload = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "user").strip().lower()
        if role not in {"system", "user", "assistant", "developer"}:
            role = "user"
        content = message.get("content")
        if content is None:
            content = ""
        payload.append({"role": role, "content": str(content)})
    return payload


def _create_with_chat(client, model_name, messages, temperature, max_tokens):
    response = client.chat.completions.create(
        model=model_name,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return (response.choices[0].message.content or "").strip()


def _create_with_responses(client, model_name, messages, temperature, max_tokens):
    kwargs = {
        "model": model_name,
        "input": _messages_to_responses_input(messages),
        "max_output_tokens": max_tokens,
    }
    if temperature is not None:
        kwargs["temperature"] = temperature
    response = client.responses.create(**kwargs)
    return _extract_responses_text(response)


def create_text_completion(client, model_name, messages, temperature=0.2, max_tokens=1500):
    prefer_responses = _model_prefers_responses(model_name)

    if prefer_responses:
        try:
            return _create_with_responses(client, model_name, messages, temperature, max_tokens)
        except Exception as exc:
            if _is_temperature_unsupported_error(exc):
                return _create_with_responses(client, model_name, messages, None, max_tokens)
            raise

    try:
        return _create_with_chat(client, model_name, messages, temperature, max_tokens)
    except Exception as exc:
        if _is_responses_only_error(exc):
            try:
                return _create_with_responses(client, model_name, messages, temperature, max_tokens)
            except Exception as resp_exc:
                if _is_temperature_unsupported_error(resp_exc):
                    return _create_with_responses(client, model_name, messages, None, max_tokens)
                raise
        raise


def create_text_completion_with_fallback(client, model_names, messages, temperature=0.2, max_tokens=1500):
    if isinstance(model_names, str):
        model_names = [model_names]

    ordered_models = []
    for raw_name in model_names or []:
        model_name = (raw_name or "").strip()
        if model_name and model_name not in ordered_models:
            ordered_models.append(model_name)

    if not ordered_models:
        raise RuntimeError("No OpenAI model names were provided.")

    errors = []
    for model_name in ordered_models:
        try:
            return (
                create_text_completion(
                    client=client,
                    model_name=model_name,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                ),
                model_name,
            )
        except Exception as exc:
            errors.append(f"{model_name}: {exc}")
            if not _is_model_not_found_error(exc):
                break

    raise RuntimeError("OpenAI generation failed. " + " | ".join(errors))
