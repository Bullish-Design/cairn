"""Deterministic type-stub generation for Monty type checking."""

from __future__ import annotations

import dataclasses
import io
import inspect
from collections.abc import Callable
from collections.abc import Sequence
from types import GenericAlias, NoneType, UnionType
from typing import Any, Union, get_args, get_origin
from typing import Callable as TypingCallable

from pydantic import BaseModel


ToolFingerprint = tuple[str, str]
CacheKey = tuple[str, str | None, tuple[ToolFingerprint, ...]]


def _model_identity(model: type[BaseModel] | None) -> str | None:
    if model is None:
        return None
    return f"{model.__module__}.{model.__qualname__}"


def _stable_annotation_repr(annotation: Any) -> str:
    if annotation is inspect.Signature.empty:
        return "<empty>"
    if annotation is NoneType or annotation is type(None):
        return "None"
    if annotation is Any:
        return "Any"

    origin = get_origin(annotation)
    if origin is not None:
        origin_text = _stable_annotation_repr(origin)
        args = ", ".join(_stable_annotation_repr(arg) for arg in get_args(annotation))
        return f"{origin_text}[{args}]"

    if isinstance(annotation, type):
        return f"{annotation.__module__}.{annotation.__qualname__}"

    if hasattr(annotation, "__module__") and hasattr(annotation, "__qualname__"):
        return f"{annotation.__module__}.{annotation.__qualname__}"

    if hasattr(annotation, "__name__"):
        return str(annotation.__name__)

    return repr(annotation)


def _tool_fingerprint(tool: Callable[..., Any]) -> ToolFingerprint:
    signature = inspect.signature(tool)
    parameters: list[str] = []
    for param in signature.parameters.values():
        default_text = (
            "<empty>" if param.default is inspect.Signature.empty else repr(param.default)
        )
        parameters.append(
            "|".join(
                [
                    param.name,
                    str(param.kind),
                    _stable_annotation_repr(param.annotation),
                    default_text,
                ]
            )
        )

    signature_text = "|".join(
        [
            ",".join(parameters),
            _stable_annotation_repr(signature.return_annotation),
        ]
    )
    return (tool.__name__, signature_text)


def _cache_key(
    *,
    input_model: type[BaseModel],
    output_model: type[BaseModel] | None,
    tools: Sequence[Callable[..., Any]],
) -> CacheKey:
    return (
        _model_identity(input_model) or "",
        _model_identity(output_model),
        tuple(_tool_fingerprint(tool) for tool in tools),
    )


class StubGenerator:
    """Generate minimal deterministic type stubs for models and tools."""

    _cache: dict[CacheKey, str] = {}

    def __init__(self) -> None:
        self._uses_any = False
        self._uses_callable = False
        self._new_types: dict[str, Any] = {}
        self._type_aliases: dict[str, Any] = {}
        self._base_models: dict[str, type[BaseModel]] = {}
        self._dataclasses: dict[str, type[Any]] = {}

    def generate(
        self,
        *,
        input_model: type[BaseModel],
        output_model: type[BaseModel] | None,
        tools: list[Callable[..., Any]],
    ) -> str:
        key = _cache_key(input_model=input_model, output_model=output_model, tools=tools)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        rendered = self._generate_uncached(
            input_model=input_model,
            output_model=output_model,
            tools=tools,
        )
        self._cache[key] = rendered
        return rendered

    def _generate_uncached(
        self,
        *,
        input_model: type[BaseModel],
        output_model: type[BaseModel] | None,
        tools: list[Callable[..., Any]],
    ) -> str:
        self._reset()
        self._collect_model_annotations(input_model)
        if output_model is not None:
            self._collect_model_annotations(output_model)

        for tool in tools:
            self._collect_tool_annotations(tool)

        import_names = ["TypedDict"]
        if self._uses_any:
            import_names.insert(0, "Any")
        if self._uses_callable:
            import_names.append("Callable")
        if self._new_types:
            import_names.append("NewType")

        output = io.StringIO()
        output.write(f"from typing import {', '.join(import_names)}")

        custom_blocks = self._render_custom_definitions(
            skip_model_names={
                input_model.__name__,
                output_model.__name__ if output_model is not None else "",
            }
        )
        output.write("\n\n\n")
        if custom_blocks:
            output.write("\n\n".join(custom_blocks))
            output.write("\n\n")

        output.write(self._model_stub(input_model))

        if output_model is not None:
            output.write("\n\n")
            output.write(self._model_stub(output_model))

        for tool in sorted(tools, key=lambda item: item.__name__):
            output.write("\n\n")
            output.write(self._tool_stub(tool))

        return output.getvalue().strip() + "\n"

    def _reset(self) -> None:
        """Clear per-run type discovery state before building a new stub set."""
        self._uses_any = False
        self._uses_callable = False
        self._new_types = {}
        self._type_aliases = {}
        self._base_models = {}
        self._dataclasses = {}

    def _render_custom_definitions(self, *, skip_model_names: set[str]) -> list[str]:
        rendered: list[str] = []
        emitted: set[str] = set()

        def emit_for_annotation(annotation: Any) -> None:
            if annotation is inspect.Signature.empty:
                return
            if self._is_new_type(annotation):
                emit_named(annotation.__name__)
                return
            if self._is_type_alias(annotation):
                emit_named(annotation.__name__)
                return
            if self._is_plain_class(annotation) and issubclass(annotation, BaseModel):
                emit_named(annotation.__name__)
                return
            if self._is_plain_class(annotation) and dataclasses.is_dataclass(annotation):
                emit_named(annotation.__name__)
                return

            origin = get_origin(annotation)
            if origin is None:
                return
            for arg in get_args(annotation):
                if arg is Ellipsis:
                    continue
                emit_for_annotation(arg)

        def emit_named(name: str) -> None:
            if name in emitted:
                return
            if name in self._new_types:
                new_type = self._new_types[name]
                emit_for_annotation(new_type.__supertype__)
                supertype = self._annotation_to_stub(new_type.__supertype__)
                rendered.append(f'{name} = NewType("{name}", {supertype})')
                emitted.add(name)
                return
            if name in self._type_aliases:
                alias = self._type_aliases[name]
                emit_for_annotation(alias.__value__)
                rendered.append(f"{name} = {self._annotation_to_stub(alias.__value__)}")
                emitted.add(name)
                return
            if name in self._base_models:
                if name in skip_model_names:
                    return
                model = self._base_models[name]
                for field in model.model_fields.values():
                    emit_for_annotation(field.annotation)
                rendered.append(self._model_stub(model))
                emitted.add(name)
                return
            if name in self._dataclasses:
                model = self._dataclasses[name]
                for field in dataclasses.fields(model):
                    emit_for_annotation(field.type)
                rendered.append(self._dataclass_stub(model))
                emitted.add(name)

        candidates = (
            set(self._new_types)
            | set(self._type_aliases)
            | set(self._base_models)
            | set(self._dataclasses)
        )
        for candidate in sorted(candidates):
            emit_named(candidate)

        return rendered

    def _model_stub(self, model: type[BaseModel]) -> str:
        lines = [f"class {model.__name__}(TypedDict):"]
        for field_name, field_info in model.model_fields.items():
            annotation = self._annotation_to_stub(field_info.annotation)
            lines.append(f"    {field_name}: {annotation}")
        if len(lines) == 1:
            lines.append("    pass")
        return "\n".join(lines)

    def _dataclass_stub(self, item: type[Any]) -> str:
        lines = [f"class {item.__name__}:"]
        for field in dataclasses.fields(item):
            annotation = self._annotation_to_stub(field.type)
            lines.append(f"    {field.name}: {annotation}")
        if len(lines) == 1:
            lines.append("    pass")
        return "\n".join(lines)

    def _tool_stub(self, tool: callable) -> str:
        signature = inspect.signature(tool)
        params: list[str] = []
        for name, param in signature.parameters.items():
            if param.kind in {param.VAR_POSITIONAL, param.VAR_KEYWORD}:
                params.append(f"*{name}" if param.kind is param.VAR_POSITIONAL else f"**{name}")
                continue
            annotation = self._annotation_to_stub(param.annotation)
            params.append(f"{name}: {annotation}")

        return_annotation = self._annotation_to_stub(signature.return_annotation)
        return f"def {tool.__name__}({', '.join(params)}) -> {return_annotation}: ..."

    def _collect_model_annotations(self, model: type[BaseModel]) -> None:
        for field_info in model.model_fields.values():
            self._collect_annotation(field_info.annotation)

    def _collect_tool_annotations(self, tool: Callable[..., Any]) -> None:
        signature = inspect.signature(tool)
        for parameter in signature.parameters.values():
            self._collect_annotation(parameter.annotation)
        self._collect_annotation(signature.return_annotation)

    def _collect_annotation(self, annotation: Any) -> None:
        if annotation is inspect.Signature.empty:
            return
        if annotation is Any:
            self._uses_any = True
            return
        if annotation is NoneType or annotation is type(None):
            return

        if self._is_new_type(annotation):
            self._new_types[annotation.__name__] = annotation
            self._collect_annotation(annotation.__supertype__)
            return

        if self._is_type_alias(annotation):
            self._type_aliases[annotation.__name__] = annotation
            self._collect_annotation(annotation.__value__)
            return

        if self._is_plain_class(annotation) and issubclass(annotation, BaseModel):
            self._base_models[annotation.__name__] = annotation
            self._collect_model_annotations(annotation)
            return

        if self._is_plain_class(annotation) and dataclasses.is_dataclass(annotation):
            self._dataclasses[annotation.__name__] = annotation
            for field in dataclasses.fields(annotation):
                self._collect_annotation(field.type)
            return

        origin = get_origin(annotation)
        if origin is None:
            return
        for arg in get_args(annotation):
            if arg is Ellipsis:
                continue
            self._collect_annotation(arg)

    def _annotation_to_stub(self, annotation: Any) -> str:
        if annotation is inspect.Signature.empty or annotation is Any:
            self._uses_any = True
            return "Any"
        if annotation is NoneType or annotation is type(None):
            return "None"

        if self._is_new_type(annotation):
            self._new_types[annotation.__name__] = annotation
            return annotation.__name__

        if self._is_type_alias(annotation):
            self._type_aliases[annotation.__name__] = annotation
            return annotation.__name__

        if self._is_plain_class(annotation) and issubclass(annotation, BaseModel):
            self._base_models[annotation.__name__] = annotation
            return annotation.__name__

        if self._is_plain_class(annotation) and dataclasses.is_dataclass(annotation):
            self._dataclasses[annotation.__name__] = annotation
            return annotation.__name__

        if self._is_plain_class(annotation):
            return annotation.__name__

        origin = get_origin(annotation)
        if origin is None:
            text = str(annotation).replace("typing.", "")
            if text == "Any":
                self._uses_any = True
            return text

        if origin in {UnionType, Union}:
            return self._render_union(get_args(annotation))

        args = [
            self._annotation_to_stub(item)
            for item in get_args(annotation)
            if item is not Ellipsis
        ]
        if origin in {list, set, frozenset}:
            inner = args[0] if args else "Any"
            self._uses_any = self._uses_any or not args
            return f"{origin.__name__}[{inner}]"
        if origin is dict:
            left = args[0] if args else "Any"
            right = args[1] if len(args) > 1 else "Any"
            self._uses_any = self._uses_any or len(args) < 2
            return f"dict[{left}, {right}]"
        if origin is tuple:
            tuple_args = get_args(annotation)
            if len(tuple_args) == 2 and tuple_args[1] is Ellipsis:
                return f"tuple[{self._annotation_to_stub(tuple_args[0])}, ...]"
            return f"tuple[{', '.join(args)}]" if args else "tuple[Any, ...]"
        if origin in {Callable, TypingCallable}:
            self._uses_callable = True
            call_args = get_args(annotation)
            if len(call_args) != 2:
                self._uses_any = True
                return "Callable[..., Any]"
            param_spec, ret_spec = call_args
            if param_spec is Ellipsis:
                params_text = "..."
            else:
                params_text = ", ".join(self._annotation_to_stub(item) for item in param_spec)
                params_text = f"[{params_text}]"
            return_text = self._annotation_to_stub(ret_spec)
            if params_text == "...":
                return f"Callable[..., {return_text}]"
            return f"Callable[{params_text}, {return_text}]"

        origin_name = self._annotation_to_stub(origin)
        return f"{origin_name}[{', '.join(args)}]" if args else origin_name

    def _render_union(self, union_args: tuple[Any, ...]) -> str:
        rendered = [self._annotation_to_stub(item) for item in union_args]
        has_none = "None" in rendered
        non_none: list[str] = []
        seen: set[str] = set()
        for item in sorted(candidate for candidate in rendered if candidate != "None"):
            if item in seen:
                continue
            seen.add(item)
            non_none.append(item)

        if has_none:
            return " | ".join([*non_none, "None"]) if non_none else "None"
        return " | ".join(non_none)

    def _is_new_type(self, annotation: Any) -> bool:
        return callable(annotation) and hasattr(annotation, "__name__") and hasattr(
            annotation, "__supertype__"
        )

    def _is_type_alias(self, annotation: Any) -> bool:
        return type(annotation).__name__ == "TypeAliasType"

    def _is_plain_class(self, annotation: Any) -> bool:
        return isinstance(annotation, type) and not isinstance(annotation, GenericAlias)
