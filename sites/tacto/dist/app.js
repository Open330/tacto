"use strict";

(() => {
  const demos = {
    replay: {
      status: "VERIFIED REPLAY",
      total: "모델 호출 0회",
      lines: [
        ["01", "새 관찰에서 입력 필드 확인", ""],
        ["02", "매개변수로 입력 · 보너스 상태 확인", ""],
        ["03", "검토 → 계산 → 완료", ""],
        ["✓", "결과 조건 검증 완료", "good"],
      ],
    },
    repair: {
      status: "CHECKED CONTINUATION",
      total: "설명용 예시 · 수리 1회",
      lines: [
        ["01", "계산 버튼의 선택자 변경 감지", "warn"],
        ["02", "실패 단계와 후보만 에이전트에 전달", ""],
        ["03", "현재 상태와 수리 후보 검증", ""],
        ["✓", "남은 단계부터 재개 · 앞 단계 유지", "good"],
      ],
    },
    stop: {
      status: "STOPPED · NO RESEND",
      total: "불확실한 완료는 재전송 금지",
      lines: [
        ["01", "완료 요청 후 결과 확인 실패", "warn"],
        ["02", "이미 처리됐을 가능성 확인", ""],
        ["03", "같은 요청을 다시 보내지 않음", ""],
        ["■", "작업 중단 · 현재 상태 검토 필요", "warn"],
      ],
    },
  };

  const tabs = [...document.querySelectorAll("[data-demo]")];
  const panel = document.getElementById("demo-panel");
  const lines = document.getElementById("demo-lines");

  function renderDemo(name) {
    const demo = demos[name];
    if (!demo) return;
    lines.replaceChildren(
      ...demo.lines.map(([number, text, tone]) => {
        const row = document.createElement("div");
        const label = document.createElement("span");
        label.className = "line-number";
        label.textContent = number;
        const content = document.createElement("span");
        content.className = tone;
        content.textContent = text;
        row.append(label, content);
        return row;
      }),
    );
    document.getElementById("demo-status").textContent = demo.status;
    document.getElementById("demo-total").textContent = demo.total;
    panel?.setAttribute("aria-labelledby", `demo-tab-${name}`);
    tabs.forEach((button) => {
      const selected = button.dataset.demo === name;
      button.classList.toggle("selected", selected);
      button.setAttribute("aria-selected", String(selected));
      button.tabIndex = selected ? 0 : -1;
    });
  }

  tabs.forEach((button, index) => {
    button.addEventListener("click", () => renderDemo(button.dataset.demo));
    button.addEventListener("keydown", (event) => {
      let next;
      if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
      if (event.key === "ArrowLeft")
        next = (index - 1 + tabs.length) % tabs.length;
      if (event.key === "Home") next = 0;
      if (event.key === "End") next = tabs.length - 1;
      if (next === undefined) return;
      event.preventDefault();
      renderDemo(tabs[next].dataset.demo);
      tabs[next].focus({ preventScroll: true });
    });
  });

  const agents = [...document.querySelectorAll("[data-agent]")];
  agents.forEach((button) =>
    button.addEventListener("click", () => {
      document.getElementById("agent-label").textContent = button.dataset.agent;
      agents.forEach((other) => {
        const selected = other === button;
        other.classList.toggle("selected", selected);
        other.setAttribute("aria-pressed", String(selected));
      });
    }),
  );

  let toastTimer;
  function toast(message) {
    const box = document.querySelector(".toast");
    box.textContent = message;
    box.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => box.classList.remove("show"), 2600);
  }

  async function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      try {
        await navigator.clipboard.writeText(text);
        return;
      } catch {
        // The same user click can still use the LAN-compatible fallback.
      }
    }
    const previousFocus = document.activeElement;
    const selection = window.getSelection();
    const ranges = selection
      ? Array.from({ length: selection.rangeCount }, (_, i) =>
          selection.getRangeAt(i).cloneRange(),
        )
      : [];
    const area = document.createElement("textarea");
    area.value = text;
    area.className = "clipboard-fallback";
    area.setAttribute("aria-label", "복사할 명령");
    document.body.append(area);
    try {
      area.focus({ preventScroll: true });
      area.select();
      if (!document.execCommand("copy")) throw new Error("Copy unavailable");
    } finally {
      area.remove();
      previousFocus?.focus({ preventScroll: true });
      if (selection && ranges.length) {
        selection.removeAllRanges();
        ranges.forEach((range) => selection.addRange(range));
      }
    }
  }

  document.querySelectorAll("[data-copy]").forEach((button) => {
    button.addEventListener("click", async () => {
      const target = document.getElementById(button.dataset.copy);
      try {
        await copyText(target.innerText.trim());
        toast("복사했습니다. 터미널 또는 에이전트에 붙여넣으세요.");
      } catch {
        toast("자동 복사가 불가능합니다. 명령을 직접 선택해 복사하세요.");
      } finally {
        button.focus({ preventScroll: true });
      }
    });
  });

  renderDemo("replay");
  document
    .querySelectorAll(
      "button[data-demo], button[data-agent], button[data-copy]",
    )
    .forEach((button) => {
      button.disabled = false;
    });
})();
