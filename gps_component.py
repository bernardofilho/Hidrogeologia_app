from __future__ import annotations

from typing import Any

import streamlit as st


_HTML_GPS = """
<div class="gps-capture-card">
  <button type="button" class="gps-capture-button" data-gps-button>
    <span aria-hidden="true">📍</span>
    <span>Usar GPS deste dispositivo</span>
  </button>
  <span class="gps-capture-status" data-gps-status>
    Toque no botão e autorize o acesso à localização.
  </span>
</div>
"""

_CSS_GPS = """
.gps-capture-card {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.65rem;
  width: 100%;
  font-family: var(--st-font, sans-serif);
}
.gps-capture-button {
  appearance: none;
  border: 1px solid var(--st-primary-color);
  border-radius: 0.55rem;
  background: var(--st-primary-color);
  color: white;
  cursor: pointer;
  font-weight: 650;
  min-height: 2.65rem;
  padding: 0.65rem 1rem;
}
.gps-capture-button:hover { filter: brightness(0.94); }
.gps-capture-button:disabled { cursor: wait; opacity: 0.7; }
.gps-capture-status {
  color: var(--st-text-color);
  font-size: 0.88rem;
  opacity: 0.82;
}
"""

_JS_GPS = """
export default function(component) {
  const { parentElement, setTriggerValue } = component;
  const button = parentElement.querySelector('[data-gps-button]');
  const status = parentElement.querySelector('[data-gps-status]');

  const setStatus = (message) => {
    if (status) status.textContent = message;
  };

  const capture = () => {
    if (!navigator.geolocation) {
      setStatus('Este navegador não disponibiliza geolocalização.');
      setTriggerValue('error', {
        code: 0,
        message: 'Geolocalização indisponível neste navegador.'
      });
      return;
    }

    if (button) button.disabled = true;
    setStatus('Obtendo coordenadas com alta precisão...');

    navigator.geolocation.getCurrentPosition(
      (position) => {
        const payload = {
          latitude: position.coords.latitude,
          longitude: position.coords.longitude,
          accuracy: position.coords.accuracy,
          altitude: position.coords.altitude,
          altitudeAccuracy: position.coords.altitudeAccuracy,
          heading: position.coords.heading,
          speed: position.coords.speed,
          timestamp: new Date(position.timestamp).toISOString()
        };
        setStatus(
          `Localização capturada. Precisão estimada: ${Math.round(position.coords.accuracy)} m.`
        );
        if (button) button.disabled = false;
        setTriggerValue('position', payload);
      },
      (error) => {
        const messages = {
          1: 'Permissão de localização negada.',
          2: 'Posição indisponível no momento.',
          3: 'Tempo esgotado ao obter a localização.'
        };
        const message = messages[error.code] || error.message || 'Falha ao obter a localização.';
        setStatus(message);
        if (button) button.disabled = false;
        setTriggerValue('error', { code: error.code, message });
      },
      {
        enableHighAccuracy: true,
        timeout: 20000,
        maximumAge: 0
      }
    );
  };

  if (button) button.addEventListener('click', capture);
  return () => {
    if (button) button.removeEventListener('click', capture);
  };
}
"""

_COMPONENTE_GPS: Any | None = None


def _obter_componente() -> Any | None:
    """Registra o componente GPS apenas uma vez por processo."""
    global _COMPONENTE_GPS
    if _COMPONENTE_GPS is not None:
        return _COMPONENTE_GPS

    componentes_v2 = getattr(getattr(st, "components", None), "v2", None)
    if componentes_v2 is None:
        return None

    _COMPONENTE_GPS = componentes_v2.component(
        "captura_gps_hidrogeologia",
        html=_HTML_GPS,
        css=_CSS_GPS,
        js=_JS_GPS,
    )
    return _COMPONENTE_GPS


def capturar_gps(key: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Monta o botão de GPS e devolve posição ou erro do navegador."""
    componente = _obter_componente()
    if componente is None:
        st.info(
            "A captura automática de GPS exige uma versão recente do Streamlit. "
            "Use a entrada manual ou por copiar e colar."
        )
        return None, None

    resultado = componente(
        key=key,
        on_position_change=lambda: None,
        on_error_change=lambda: None,
        height=72,
    )
    posicao = getattr(resultado, "position", None)
    erro = getattr(resultado, "error", None)
    return posicao, erro
