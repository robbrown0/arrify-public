import XCTest
final class LaunchTests: XCTestCase {
    @MainActor func testSignInIsAccessibleAndDoesNotAutoplay() {
        let app = XCUIApplication()
        app.launchEnvironment["ARRIFY_UI_TESTING"] = "1"
        app.launch()
        XCTAssertTrue(app.textFields["serverAddress"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.textFields["username"].exists)
        XCTAssertTrue(app.secureTextFields["password"].exists)
        XCTAssertFalse(app.buttons["signIn"].isEnabled)
        XCTAssertFalse(app.buttons["Pause"].exists)
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = "Arrify sign-in"; attachment.lifetime = .keepAlways
        add(attachment)
    }
    @MainActor func testSignInBrowseSearchAndSignOut() {
        let app = XCUIApplication()
        app.launchEnvironment["ARRIFY_UI_TESTING"] = "1"
        app.launch()
        let username = app.textFields["username"]
        XCTAssertTrue(username.waitForExistence(timeout: 10))
        XCTAssertTrue(app.descendants(matching: .any)["privacyPolicy"].firstMatch.exists)
        username.tap(); username.typeText("listener")
        let password = app.secureTextFields["password"]
        password.tap(); password.typeText("fixture")
        app.buttons["signIn"].tap()
        XCTAssertTrue(app.buttons["Songs"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts["On the record."].waitForExistence(timeout: 5))
        let homeShot = XCTAttachment(screenshot: app.screenshot()); homeShot.name = "Artwork-led Home"; homeShot.lifetime = .keepAlways; add(homeShot)
        app.buttons["refreshLibrary"].tap()
        XCTAssertFalse(app.staticTexts["Library updated"].exists)
        app.buttons["Songs"].tap()
        XCTAssertTrue(app.buttons["Play Just Like a Pill by P!nk"].waitForExistence(timeout: 10))
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = "Arrify library"; attachment.lifetime = .keepAlways; add(attachment)
        let search = app.searchFields.firstMatch
        if !search.waitForExistence(timeout: 5) {
            app.buttons["Search"].firstMatch.tap()
        }
        XCTAssertTrue(search.waitForExistence(timeout: 5))
        search.tap(); search.typeText("missing")
        XCTAssertTrue(app.staticTexts["No matching songs"].waitForExistence(timeout: 10))
        let closeSearch = app.buttons["Close"].exists ? app.buttons["Close"] : app.buttons["Cancel"]
        closeSearch.tap()
        app.buttons["Settings"].firstMatch.tap()
        XCTAssertTrue(app.buttons["Sign out"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.descendants(matching: .any)["privacyPolicy"].firstMatch.exists)
        app.buttons["Sign out"].tap()
        XCTAssertTrue(app.textFields["username"].waitForExistence(timeout: 5))
    }
}

extension LaunchTests {
    @MainActor func testAlbumsChannelsAndMoodNavigation() {
        let app = XCUIApplication()
        app.launchEnvironment["ARRIFY_UI_TESTING"] = "1"
        app.launch()
        XCTAssertTrue(app.textFields["username"].waitForExistence(timeout: 10))
        app.textFields["username"].tap(); app.textFields["username"].typeText("listener")
        app.secureTextFields["password"].tap(); app.secureTextFields["password"].typeText("fixture")
        app.buttons["signIn"].tap()
        XCTAssertTrue(app.buttons["Albums"].waitForExistence(timeout: 10))
        app.buttons["Albums"].tap()
        XCTAssertTrue(app.staticTexts["M!ssundaztood"].waitForExistence(timeout: 10))
        app.staticTexts["M!ssundaztood"].firstMatch.tap()
        XCTAssertTrue(app.buttons["playCollection"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.buttons["shuffleCollection"].exists)
        let album = XCTAttachment(screenshot: app.screenshot()); album.name = "Album detail"; album.lifetime = .keepAlways; add(album)
        app.buttons["Channels"].firstMatch.tap()
        XCTAssertTrue(app.buttons["Play 80s on 8"].waitForExistence(timeout: 10))
        let channels = XCTAttachment(screenshot: app.screenshot()); channels.name = "Channels"; channels.lifetime = .keepAlways; add(channels)
        app.buttons["Library"].firstMatch.tap()
        app.buttons["Moods"].firstMatch.tap()
        XCTAssertTrue(app.buttons["mood-feel-good"].waitForExistence(timeout: 10))
        app.buttons["mood-feel-good"].tap()
        XCTAssertTrue(app.buttons["Play Feel Good Favorites"].waitForExistence(timeout: 10))
        let mood = XCTAttachment(screenshot: app.screenshot()); mood.name = "Mood playlists"; mood.lifetime = .keepAlways; add(mood)
    }
}

extension LaunchTests {
    @MainActor func testLastChannelClearsMiniPlayerAndRepeatControls() {
        let app = XCUIApplication(); app.launchEnvironment["ARRIFY_UI_TESTING"] = "1"; app.launch()
        XCTAssertTrue(app.textFields["username"].waitForExistence(timeout: 10))
        app.textFields["username"].tap(); app.textFields["username"].typeText("listener")
        app.secureTextFields["password"].tap(); app.secureTextFields["password"].typeText("fixture")
        app.buttons["signIn"].tap()
        XCTAssertTrue(app.buttons["Songs"].waitForExistence(timeout: 10))
        app.buttons["Songs"].tap()
        let play = app.buttons["Play Just Like a Pill by P!nk"]
        XCTAssertTrue(play.waitForExistence(timeout: 10)); play.tap()
        let nowPlaying = app.buttons["expandPlayer"]
        XCTAssertTrue(nowPlaying.waitForExistence(timeout: 10), app.debugDescription)
        app.buttons["Channels"].firstMatch.tap()
        let last = app.buttons["Play Station 12"]
        for _ in 0..<10 {
            if last.exists && last.isHittable && last.frame.maxY < nowPlaying.frame.minY { break }
            app.scrollViews.firstMatch.swipeUp()
        }
        XCTAssertTrue(last.isHittable)
        XCTAssertLessThan(last.frame.maxY, nowPlaying.frame.minY)
        let shot = XCTAttachment(screenshot: app.screenshot()); shot.name = "Last channel above player"; shot.lifetime = .keepAlways; add(shot)
        nowPlaying.tap()
        let close = app.buttons["Close player"]
        XCTAssertTrue(close.waitForExistence(timeout: 10))
        XCTAssertLessThan(close.frame.minY, app.frame.height * 0.15, "Expanded player must occupy the full screen")
        let repeatButton = app.buttons["Repeat"]
        XCTAssertTrue(repeatButton.waitForExistence(timeout: 10), app.debugDescription)
        if !repeatButton.isHittable { app.scrollViews.firstMatch.swipeUp() }
        XCTAssertTrue(app.buttons["Shuffle upcoming songs"].exists)
        XCTAssertEqual(repeatButton.value as? String, "off")
        repeatButton.tap()
        expectation(for: NSPredicate(format: "value == 'all'"), evaluatedWith: repeatButton)
        waitForExpectations(timeout: 5)
        XCTAssertLessThan(app.buttons["upNext"].frame.maxY, app.frame.maxY - 12, "Footer must fit above the home indicator")
        let playerShot = XCTAttachment(screenshot: app.screenshot()); playerShot.name = "Redesigned player"; playerShot.lifetime = .keepAlways; add(playerShot)
        app.buttons["upNext"].tap()
        XCTAssertTrue(app.staticTexts["Family Portrait"].waitForExistence(timeout: 5))
        app.buttons["Done"].tap()
        app.buttons["artworkDisplayButton"].tap()
        XCTAssertTrue(app.buttons["Close artwork"].waitForExistence(timeout: 5))
        app.buttons["Close artwork"].tap()
    }
}
