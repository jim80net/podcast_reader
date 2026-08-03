package net.jim80.podcastreader.ui.account

import android.app.Activity
import android.content.Context
import android.content.ContextWrapper
import android.view.WindowManager
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import net.jim80.podcastreader.core.premium.OnlineUnavailableReason
import net.jim80.podcastreader.runtime.AccountConnectionIssue
import net.jim80.podcastreader.ui.AccountUiState

@Composable
fun AccountScreen(
    state: AccountUiState,
    onDevelopmentOriginChanged: (String) -> Unit,
    onConnect: () -> Unit,
    onCancelConnect: () -> Unit,
    onRetry: () -> Unit,
    onSignOut: () -> Unit,
    modifier: Modifier = Modifier,
) {
    SecureContentEffect(state is AccountUiState.Authorizing)
    Column(modifier = modifier, verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text("Account", style = MaterialTheme.typography.headlineMedium)
        Text(
            "Your online account is optional and separate from Connect this computer.",
            style = MaterialTheme.typography.bodyLarge,
        )
        when (state) {
            AccountUiState.Bootstrapping -> {
                Text("Checking account status", style = MaterialTheme.typography.titleMedium)
                Text("Local reading remains available while the account record is checked.")
            }
            AccountUiState.Connecting -> {
                Text("Starting development connection", style = MaterialTheme.typography.titleMedium)
                Text("The local reader remains available while the development service responds.")
                OutlinedButton(onClick = onCancelConnect) { Text("Cancel") }
            }
            is AccountUiState.Local -> LocalAccount(state, onDevelopmentOriginChanged, onConnect)
            is AccountUiState.Authorizing -> AuthorizingAccount(state, onCancelConnect)
            AccountUiState.OnlineFree -> ConnectedAccount(
                title = "Online free",
                detail = "Local reading stays available. Eligible Library and Jobs screens may show plain-text house messages.",
                onSignOut = onSignOut,
            )
            AccountUiState.OnlinePremium -> ConnectedAccount(
                title = "Premium",
                detail = "Your current online entitlement is premium and ad-free.",
                onSignOut = onSignOut,
            )
            is AccountUiState.OnlineUnavailable -> UnavailableAccount(state.reason, onRetry, onSignOut)
        }
    }
}

@Composable
private fun LocalAccount(
    state: AccountUiState.Local,
    onDevelopmentOriginChanged: (String) -> Unit,
    onConnect: () -> Unit,
) {
    Text("No online account connected", style = MaterialTheme.typography.titleMedium)
    Text("The local reader works without an online account.")
    Text(
        "Development service — not the production account service",
        color = MaterialTheme.colorScheme.error,
    )
    Text("Enter the canonical HTTPS origin supplied for development. No service is configured by default.")
    OutlinedTextField(
        value = state.developmentOriginDraft,
        onValueChange = onDevelopmentOriginChanged,
        label = { Text("Development service origin") },
        placeholder = { Text("https://…") },
        singleLine = true,
        modifier = Modifier.fillMaxWidth(),
    )
    state.connectionIssue?.let { issue ->
        Text(issue.readerMessage(), color = MaterialTheme.colorScheme.error)
    }
    Button(onClick = onConnect, enabled = state.developmentOriginValid) {
        Text("Connect development account")
    }
}

@Composable
private fun AuthorizingAccount(state: AccountUiState.Authorizing, onCancel: () -> Unit) {
    Text(
        "Development service — not the production account service",
        color = MaterialTheme.colorScheme.error,
    )
    Text("Finish connecting in your browser", style = MaterialTheme.typography.titleMedium)
    Text("Enter this one-time code:")
    Text(state.userCode.value, style = MaterialTheme.typography.headlineLarge)
    Text("This code expires at ${state.expiresAt}.", color = MaterialTheme.colorScheme.onSurfaceVariant)
    OutlinedButton(onClick = onCancel) { Text("Cancel") }
}

private fun AccountConnectionIssue.readerMessage(): String = when (this) {
    AccountConnectionIssue.INVALID_DEVELOPMENT_ORIGIN ->
        "Enter one canonical HTTPS origin with no path, query, fragment, or credentials."
    AccountConnectionIssue.CONNECTION_FAILED ->
        "The development account connection could not be completed. Local reading is unchanged."
    AccountConnectionIssue.ACCESS_DENIED -> "The development account connection was denied."
    AccountConnectionIssue.AUTHORIZATION_EXPIRED ->
        "The development account code expired. Start a new connection."
}

@Composable
private fun ConnectedAccount(title: String, detail: String, onSignOut: () -> Unit) {
    Text(title, style = MaterialTheme.typography.titleMedium)
    Text(detail)
    OutlinedButton(onClick = onSignOut) { Text("Sign out") }
}

@Composable
private fun UnavailableAccount(
    reason: OnlineUnavailableReason,
    onRetry: () -> Unit,
    onSignOut: () -> Unit,
) {
    Text("Online features unavailable", style = MaterialTheme.typography.titleMedium)
    Text(reason.readerMessage())
    Text("Your home-engine connection and local reader are unchanged.")
    Row(horizontalArrangement = Arrangement.spacedBy(12.dp), modifier = Modifier.fillMaxWidth()) {
        Button(onClick = onRetry) { Text("Try again") }
        OutlinedButton(onClick = onSignOut) { Text("Sign out") }
    }
}

private fun OnlineUnavailableReason.readerMessage(): String = when (this) {
    OnlineUnavailableReason.OFFLINE -> "The premium service is offline. No ads are fetched or shown."
    OnlineUnavailableReason.UNAUTHORIZED -> "The account session ended. Reconnect to use online features."
    OnlineUnavailableReason.STALE -> "The last account status expired. It is not treated as free or premium."
    OnlineUnavailableReason.INCOMPATIBLE_RESPONSE -> "The premium service returned an unsupported response."
    OnlineUnavailableReason.LOCAL_CREDENTIAL_STORAGE ->
        "The saved account could not be removed from secure storage. You are not signed out yet."
}

@Composable
private fun SecureContentEffect(enabled: Boolean) {
    val activity = LocalContext.current.findActivity()
    DisposableEffect(activity, enabled) {
        val secureFlags = activity?.window?.attributes?.flags ?: 0
        val wasSecure = secureFlags.and(WindowManager.LayoutParams.FLAG_SECURE) != 0
        if (enabled) activity?.window?.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        onDispose {
            if (enabled && !wasSecure) activity?.window?.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
        }
    }
}

private tailrec fun Context.findActivity(): Activity? = when (this) {
    is Activity -> this
    is ContextWrapper -> baseContext.findActivity()
    else -> null
}
