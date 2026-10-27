"""Small, closed recovery hints; never interpolate screen or Vault contents."""
HINTS={
 'credential_target_missing':('login_field_missing','현재 로그인 화면에서 해당 필드를 다시 지정하세요.'),
 'credential_scope_mismatch':('destination_changed','위임한 앱·사이트와 현재 화면이 같은지 확인하세요.'),
 'credential_fill_failed':('field_input_failed','해당 필드의 상태를 확인하고 그 단계만 수정하세요.'),
 'credential_login_unverified':('login_result_pending','제출한 결과를 먼저 확인하세요. 제출 명령은 반복하지 않았습니다.'),
 'credential_submit_unverified':('submit_result_unknown','현재 결과를 확인한 뒤 이어갈 단계를 선택하세요.'),
 'credential_account_mismatch':('different_account','현재 계정이 위임한 계정과 다릅니다. 계정 전환 경로를 사용하세요.'),
 'vault_locked':('vault_locked','연결된 Vault의 기존 인증 세션을 확인하세요.'),
 'credential_file_unavailable':('session_file_missing','등록한 세션 파일 경로를 확인하거나 기존 BW_SESSION을 사용하세요.'),
 'credential_grant_expired':('delegation_expired','이 위임의 만료 설정을 확인하세요.'),
 'credential_vault_changed':('vault_configuration_changed','Vault 연결이 바뀌었습니다. 이 연결을 사용할 위임을 갱신하세요.'),
}

def recovery(code,*,field=None,stage_index=0,submitted=False,completed_stages=0):
    reason,hint=HINTS.get(code,('credential_step_failed','이 단계의 연결과 화면 상태를 확인하세요.'))
    return {'reason':reason,'hint':hint,'field':field,'stage_index':stage_index,
            'completed_stages':completed_stages,'submit_repeated':False,
            'inspect_before_submit':submitted}
