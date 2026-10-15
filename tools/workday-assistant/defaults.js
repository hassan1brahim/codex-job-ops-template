// Fill these in from the extension Options page, or edit them locally. Never commit real values.
const WORKDAY_DEFAULT_PROFILE = Object.freeze({
  firstName: "",
  lastName: "",
  email: "",
  accountPassword: "",
  phone: "",
  phoneCountryCode: "United States of America (+1)",
  country: "United States of America",
  state: "",
  hearAboutUs: "LinkedIn",
  previouslyWorkedForEmployer: "No",
  city: "",
  postalCode: "",
  address1: "",
  address2: "",
  linkedin: "",
  github: "",
  website: "",
  workAuthorizedUS: "Yes",
  sponsorshipRequired: "No",
  phoneType: "Main/Home",
  resumePath: "",
  education: {
    school: "",
    degree: "",
    field: "",
    startMonth: "",
    startYear: "",
    graduationMonth: "",
    graduationYear: ""
  }
});

async function getWorkdayProfile() {
  const stored = await chrome.storage.local.get("workdayProfile");
  const profile = deepMerge(WORKDAY_DEFAULT_PROFILE, stored.workdayProfile || {});
  if (!profile.resumePath) profile.resumePath = WORKDAY_DEFAULT_PROFILE.resumePath;
  return profile;
}

function deepMerge(base, update) {
  const result = { ...base };
  for (const [key, value] of Object.entries(update || {})) {
    if (value && typeof value === "object" && !Array.isArray(value)) {
      result[key] = deepMerge(base[key] || {}, value);
    } else {
      result[key] = value;
    }
  }
  return result;
}
