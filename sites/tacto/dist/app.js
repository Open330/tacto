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
        ["check", "결과 조건 검증 완료", "good"],
      ],
    },
    repair: {
      status: "CHECKED CONTINUATION",
      total: "설명용 예시 · 수리 1회",
      lines: [
        ["01", "계산 버튼의 선택자 변경 감지", "warn"],
        ["02", "실패 단계와 후보만 에이전트에 전달", ""],
        ["03", "현재 상태와 수리 후보 검증", ""],
        ["check", "남은 단계부터 재개 · 앞 단계 유지", "good"],
      ],
    },
    stop: {
      status: "STOPPED · NO RESEND",
      total: "불확실한 완료는 재전송 금지",
      lines: [
        ["01", "완료 요청 후 결과 확인 실패", "warn"],
        ["02", "이미 처리됐을 가능성 확인", ""],
        ["03", "같은 요청을 다시 보내지 않음", ""],
        ["stop", "작업 중단 · 현재 상태 검토 필요", "warn"],
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
        if (number === "check" || number === "stop") {
          const svg = document.createElementNS(
            "http://www.w3.org/2000/svg",
            "svg",
          );
          svg.classList.add("ui-icon");
          svg.setAttribute("aria-hidden", "true");
          svg.setAttribute("focusable", "false");
          svg.setAttribute("viewBox", "0 0 24 24");
          const use = document.createElementNS(
            "http://www.w3.org/2000/svg",
            "use",
          );
          use.setAttribute("href", `assets/ui-icons.svg#${number}`);
          svg.append(use);
          label.append(svg);
        } else {
          label.textContent = number;
        }
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

  // Keep an unloaded video on its poster until the user requests playback.
  // Native controls remain the no-JavaScript fallback and take over on play.
  document.querySelectorAll("video").forEach((video) => {
    const shell = document.createElement("div");
    shell.className = "video-shell";
    video.before(shell);
    shell.append(video);
    const start = document.createElement("button");
    start.type = "button";
    start.className = "video-start";
    start.setAttribute(
      "aria-label",
      `영상 재생: ${video.getAttribute("aria-label") || "실행 예제"}`,
    );
    start.innerHTML =
      '<svg class="ui-icon" aria-hidden="true" viewBox="0 0 24 24"><use href="assets/ui-icons.svg#play"></use></svg><span>영상 재생</span>';
    shell.append(start);
    video.controls = false;
    video.addEventListener("play", () => {
      video.controls = true;
      start.hidden = true;
    });
    start.addEventListener("click", async () => {
      video.controls = true;
      start.hidden = true;
      try {
        await video.play();
      } catch {
        // Leave the native controls available when playback needs another click.
        video.controls = true;
      }
    });
  });

  // Reuse the gallery's recording metadata; never preload all carousel videos.
  const carousel = document.getElementById("task-carousel");
  if (carousel) {
    const video = carousel.querySelector("video");
    const start = carousel.querySelector(".video-start");
    const choices = [...carousel.querySelectorAll("[data-case]")];
    const rotation = document.getElementById("carousel-rotation");
    const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const descriptions = {
      textedit: "TextEdit · 메모 입력 후 별도 저장 단계에서 디스크 내용 일치 확인. 자동으로 끝까지 완료한 사례는 아닙니다.",
      finder: "Finder · 촬영용 폴더의 파일 5개를 종류별·이름순으로 정렬. 파일 이동·삭제는 하지 않았습니다.",
      translate: "Google Translate · 영어 예문 입력 후 한국어 결과 표시 확인. 전송 단계의 완료 증명은 미확정입니다.",
      markdown: "Markdown Live Preview · 예제 앞부분을 수정해 제목·목록·굵은 글씨 렌더링 확인. 전체 문서 저장 사례는 아닙니다.",
      units: "UnitConverters.net · 3.5미터를 11.482939633피트로 변환하고 별도 계산식과 대조했습니다.",
      wikipedia: "Wikipedia · 서울을 검색하고 History 항목으로 이동했습니다.",
    };
    let index = 0;
    let rotating = !motion.matches;
    let hovering = false;
    let timer;
    const updateRotation = () => {
      rotation.textContent = rotating ? "자동 넘김 멈추기" : "자동 넘김 시작하기";
      clearTimeout(timer);
      if (rotating && !hovering && !document.hidden && video.paused &&
          (!carousel.contains(document.activeElement) || document.activeElement === rotation)) {
        timer = setTimeout(() => { show(index + 1); }, 8000);
      }
    };
    function show(next) {
      index = (next + choices.length) % choices.length;
      const key = choices[index].dataset.case;
      const card = document.getElementById(`example-${key}`);
      const recording = card.querySelector("video");
      video.pause();
      video.controls = false;
      video.poster = recording.poster;
      video.setAttribute("aria-label", recording.getAttribute("aria-label"));
      video.querySelector("source").src = recording.querySelector("source").src;
      video.querySelector("track").src = recording.querySelector("track").src;
      video.querySelector("a").href = recording.querySelector("source").src;
      // load() would fetch media on some browsers despite preload=none.
      // Changing src while preload=none resets playback without eager downloads.
      video.src = recording.querySelector("source").src;
      start.hidden = false;
      start.setAttribute("aria-label", `영상 재생: ${recording.getAttribute("aria-label")}`);
      document.getElementById("carousel-title").textContent = card.querySelector("blockquote").textContent.trim();
      document.getElementById("carousel-description").textContent = descriptions[key];
      document.getElementById("carousel-detail").href = `#example-${key}`;
      document.getElementById("carousel-count").textContent = `${index + 1} / ${choices.length}`;
      choices.forEach((choice, i) => choice.setAttribute("aria-pressed", String(i === index)));
      updateRotation();
    }
    choices.forEach((choice, i) => choice.addEventListener("click", () => {
      rotating = false;
      show(i);
    }));
    document.getElementById("carousel-prev").addEventListener("click", () => { rotating = false; show(index - 1); });
    document.getElementById("carousel-next").addEventListener("click", () => { rotating = false; show(index + 1); });
    rotation.addEventListener("click", () => { rotating = !rotating; updateRotation(); });
    carousel.addEventListener("mouseenter", () => { hovering = true; updateRotation(); });
    carousel.addEventListener("mouseleave", () => { hovering = false; updateRotation(); });
    // Keyboard focus permanently pauses automatic rotation until explicitly restarted.
    carousel.addEventListener("focusin", (event) => {
      if (event.target === rotation) clearTimeout(timer);
      else { rotating = false; updateRotation(); }
    });
    carousel.addEventListener("focusout", () => setTimeout(updateRotation, 0));
    video.addEventListener("play", () => { rotating = false; updateRotation(); });
    document.addEventListener("visibilitychange", updateRotation);
    motion.addEventListener("change", () => { if (motion.matches) rotating = false; updateRotation(); });
    carousel.querySelector(".carousel-toolbar").hidden = false;
    carousel.querySelector(".carousel-navigation").hidden = false;
    updateRotation();
  }

  const filters = document.querySelector(".example-filters");
  const cases = [...document.querySelectorAll(".everyday-case")];
  if (filters && cases.length) {
    filters.hidden = false;
    filters.querySelectorAll("button").forEach((button) => {
      button.addEventListener("click", () => {
        const kind = button.dataset.exampleFilter;
        filters
          .querySelectorAll("button")
          .forEach((item) =>
            item.setAttribute("aria-pressed", String(item === button)),
          );
        cases.forEach((card) => {
          card.hidden = kind !== "all" && card.dataset.category !== kind;
          if (card.hidden) card.querySelector("video")?.pause();
        });
        document.querySelector("#example-count").textContent =
          `${cases.filter((card) => !card.hidden).length}개 사례를 표시합니다.`;
      });
    });
  }

  renderDemo("replay");
  document
    .querySelectorAll(
      "button[data-demo], button[data-agent], button[data-copy]",
    )
    .forEach((button) => {
      button.disabled = false;
    });
})();
