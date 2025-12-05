document.querySelector("#loginForm").addEventListener("submit", async (e) => {
  e.preventDefault();

  const email = document.querySelector("#loginEmail").value;
  const password = document.querySelector("#loginPassword").value;

  const response = await fetch("/login", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: `email=${encodeURIComponent(email)}&password=${encodeURIComponent(password)}`
  });

  if (response.redirected) {
    window.location.href = response.url;
  } else {
    const text = await response.text();
    if (text.includes("Welcome") || text.includes("Dashboard")) {
      window.location.href = "/dashboard";
    } else {
      alert("Invalid email or password. Please try again.");
    }
  }
});

// Signup modal functionality
document.addEventListener("DOMContentLoaded", function() {
  const signupOverlay = document.getElementById("signupOverlay");
  const showSignupBtn = document.getElementById("showSignup");
  const openSignupLink = document.getElementById("openSignup");
  const closeSignupLink = document.getElementById("closeSignup");
  const backToLoginLink = document.getElementById("backToLoginLink");
  const dismissSignup = document.getElementById("dismissSignup");

  // Show signup modal
  function showSignup() {
    signupOverlay.style.display = "flex";
  }

  // Hide signup modal
  function hideSignup() {
    signupOverlay.style.display = "none";
  }

  // Event listeners
  if (showSignupBtn) showSignupBtn.addEventListener("click", showSignup);
  if (openSignupLink) openSignupLink.addEventListener("click", showSignup);
  if (closeSignupLink) closeSignupLink.addEventListener("click", hideSignup);
  if (backToLoginLink) backToLoginLink.addEventListener("click", hideSignup);
  if (dismissSignup) dismissSignup.addEventListener("click", hideSignup);

  // Close modal when clicking outside
  signupOverlay.addEventListener("click", function(e) {
    if (e.target === signupOverlay) {
      hideSignup();
    }
  });

  document.addEventListener("keydown", function(e){
  if (e.key === "Escape" && signupOverlay.style.display !== "none") {
    hideSignup();
  }
});

  // Signup form submission
  const signupForm = document.getElementById("signupForm");
  if (signupForm) {
    signupForm.addEventListener("submit", async function(e) {
      e.preventDefault();
      const firstName = document.getElementById("firstName").value;
      const lastName = document.getElementById("lastName").value;
      const email = document.getElementById("signupEmail").value;
      const password = document.getElementById("signupPassword").value;

      const resp = await fetch("/signup", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: `firstName=${encodeURIComponent(firstName)}&lastName=${encodeURIComponent(lastName)}&email=${encodeURIComponent(email)}&password=${encodeURIComponent(password)}`
      });

      const text = await resp.text();
      if (resp.ok && (text.includes("Signed up") || text.includes("Welcome"))) {
        window.location.href = "/dashboard";
      } else {
        alert(text || "Signup failed. Please try again.");
      }
    });
  }
});
const backToLoginLink = document.getElementById('backToLoginLink');
const signupOverlayEl = document.getElementById('signupOverlay');

if (backToLoginLink) {
  backToLoginLink.addEventListener('click', (e) => {
    e.preventDefault();
    // close the modal
    signupOverlayEl.style.display = 'none';
    // optional: reset form
    const sf = document.getElementById('signupForm');
    if (sf) sf.reset();
    // focus login email for convenience
    const loginEmail = document.getElementById('loginEmail');
    if (loginEmail) loginEmail.focus();
  });
}