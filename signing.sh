# The identity each half of the boundary claims, and requires of the other.
#
# This one text is stamped into a signature as its DESIGNATED REQUIREMENT and is
# the acceptance predicate the other side applies (`clientRequirement` in
# NLCHelper.swift, `helperRequirement` in nlc.swift). Three copies that have to
# read alike, so they are written once here and compared by
# tests/test_signing_requirements.py.
#
# Left alone, codesign writes a requirement that pins the leaf certificate's CN
# — one developer, one certificate, and an identity that dies on the day that
# certificate expires. The team's OU is what we actually mean: this identifier,
# signed by this team, under Apple's anchor. `anchor apple generic` by itself
# only says "some Apple-issued identity", so the team is named.
NLC_TEAM_ID=48H4RZNB97
NLC_LAUNCHER_ID=ai.callabo.nlc
NLC_HELPER_ID=ai.callabo.nlc.helper

# $1 = signing identifier
nlc_requirement() {
  printf 'identifier "%s" and anchor apple generic and certificate leaf[subject.OU] = "%s"' \
         "$1" "$NLC_TEAM_ID"
}
