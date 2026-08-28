document.querySelectorAll("[data-copy]").forEach((button) => {
  button.addEventListener("click", async () => {
    const target = document.getElementById(button.dataset.copy);
    if (!target) return;
    try {
      await navigator.clipboard.writeText(target.textContent.trim());
      button.textContent = "Copied";
      button.classList.add("copied");
      window.setTimeout(() => {
        button.textContent = "Copy";
        button.classList.remove("copied");
      }, 1600);
    } catch {
      window.getSelection()?.selectAllChildren(target);
    }
  });
});

const demoMount = document.getElementById("demo-player");
const demoStatus = document.querySelector("[data-demo-status]");
const toggleButton = document.querySelector('[data-demo-action="toggle"]');

if (demoMount && window.AsciinemaPlayer) {
  let playing = true;
  demoMount.textContent = "";

  const player = window.AsciinemaPlayer.create("assets/demo.cast?v=8", demoMount, {
    autoPlay: true,
    controls: true,
    fit: "both",
    idleTimeLimit: 3,
    loop: false,
    poster: "npt:0:00",
    speed: 1,
    terminalFontFamily: '"SFMono-Regular", Consolas, "Liberation Mono", monospace',
    terminalLineHeight: 1.15,
    theme: "dracula"
  });

  const setPlaybackState = (isPlaying, status) => {
    playing = isPlaying;
    if (toggleButton) {
      toggleButton.textContent = isPlaying ? "Pause" : "Play";
      toggleButton.setAttribute("aria-label", isPlaying ? "Pause demo" : "Play demo");
    }
    if (demoStatus) demoStatus.textContent = status;
  };

  const seekBy = async (seconds) => {
    const currentTime = await player.getCurrentTime();
    await player.seek(Math.max(0, currentTime + seconds));
  };

  player.addEventListener("playing", () => setPlaybackState(true, "Playing"));
  player.addEventListener("pause", () => setPlaybackState(false, "Paused"));
  player.addEventListener("ended", () => setPlaybackState(false, "Finished — replay available"));

  document.querySelector('[data-demo-action="rewind"]')?.addEventListener("click", () => seekBy(-5));
  document.querySelector('[data-demo-action="forward"]')?.addEventListener("click", () => seekBy(5));
  toggleButton?.addEventListener("click", () => {
    if (playing) player.pause();
    else player.play();
  });
  document.querySelector('[data-demo-action="replay"]')?.addEventListener("click", async () => {
    await player.seek(0);
    await player.play();
  });

  window.__claudeOpenRouterDemo = player;
} else if (demoStatus) {
  demoStatus.textContent = "Open recording";
}
